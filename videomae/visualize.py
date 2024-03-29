# import matplotlib.pyplot as plt

from datasets import build_pretraining_dataset
import torch
import torch.nn.functional as F

from timm.models import create_model
import modeling_pretrain
from pathlib import Path
import argparse
from config_utils import parse_yml, combine

import numpy as np
from einops import rearrange
from flow_vis import flow_to_color
from torchvision.utils import save_image
import cv2

def get_args():
    parser = argparse.ArgumentParser('VideoMAE pre-training script', add_help=False)

    # Dataset parameters

    parser.add_argument('--device', default='cuda',
                        help='device to use for training / testing')
    # configuration file
    parser.add_argument('--config', default='none', type=str,
                        help='path to configuration file')

    parser.add_argument('--ckpt', default='none', type=str,
                        help='path to checkpoint')

    parser.add_argument('--overwrite', default='command-line', type=str,
                        help='overwrite args in command-line or configuration file')
    return parser.parse_args()


def get_model(args):
    print(f"Creating model: {args.model}")

    if not args.ts_pretrain:
        model = create_model(
            args.model,
            pretrained=False,
            drop_path_rate=args.drop_path,
            drop_block_rate=None,
            decoder_depth=args.decoder_depth
        )
    else:
        model = create_model(
            args.model,
            pretrained=False,
            drop_path_rate=args.drop_path,
            drop_block_rate=None,
            decoder_depth=args.decoder_depth,

            version = args.version,
            use_rgb_stat = args.use_rgb_stat, 
            share_within_modality_proj_layer = args.share_within_modality_proj_layer,
            mask_tokenizer = args.mask_tokenizer,
            share_proj_layer = args.share_proj_layer,
            fuse_scheme = args.fuse_scheme,
            tokenizer_backbone = args.tokenizer_backbone,
        )
    return model


@torch.no_grad()
def main(args):
    device = args.device
    model = get_model(args)

    checkpoints = []
    for ckpt in args.ckpt.split(","):
        checkpoints.append(torch.load(ckpt, map_location='cpu'))

    if args.ts_pretrain:
        patch_size = model.rgb_encoder.patch_embed.patch_size
    else:
        patch_size = model.encoder.patch_embed.patch_size

    # import sys
    # sys.exit(0)
    args.window_size = (args.num_frames // 2, args.input_size // patch_size[0], args.input_size // patch_size[1])
    args.patch_size = patch_size

    dataset_train = build_pretraining_dataset(args, cache_manager=None)

    data_loader_train = torch.utils.data.DataLoader(
        dataset_train,
        batch_size=1,
        num_workers=1,
        pin_memory=True,
        shuffle=False,
        # worker_init_fn=utils.seed_worker
    )

    mean = torch.tensor([0.485, 0.456, 0.406]).reshape(3, 1, 1, 1).repeat(1, 1, 224, 224).to(device)
    std = torch.tensor([0.229, 0.224, 0.225]).reshape(3, 1, 1, 1).repeat(1, 1, 224, 224).to(device)

    model.to(device)
    model.eval()
    for i, batch in enumerate(data_loader_train):

        for j, weights in enumerate(checkpoints):
            model.load_state_dict(weights['model'], strict=True)

            if args.ts_pretrain:
                frame, mask, flows = batch[0].to(device), batch[1].to(device), batch[2].to(device)
                mask = mask.flatten(1).to(torch.bool).cpu()
                output = model(frame, flows, mask, all_token=True)
            elif args.flow_mode != "":
                frame, mask, flows = batch[0].to(device), batch[1].to(device), batch[2].to(device)
                mask = mask.flatten(1).to(torch.bool).cpu()
                output = model(frame, mask, all_token=True)
            else:
                frame, mask = batch[0].to(device), batch[1].to(device)
                mask = mask.flatten(1).to(torch.bool).cpu()
                output = model(frame, mask, all_token=True)

            # de-normalize input frames
            unnormed_frame = frame.squeeze() * std + mean
            # print(unnormed_frame[:, 0, :10, :10]*255)
            unnormed_frame_pre = unnormed_frame[:, 0, ...].cpu().numpy().transpose(1, 2, 0)*255
            unnormed_frame_pre = cv2.cvtColor(unnormed_frame_pre, cv2.COLOR_BGR2RGB)
            unnormed_frame_post = unnormed_frame[:, 1, ...].cpu().numpy().transpose(1, 2, 0)*255
            unnormed_frame_post = cv2.cvtColor(unnormed_frame_post, cv2.COLOR_BGR2RGB)

            if not args.ts_pretrain and args.flow_mode == "local":

                # raw version
                # flow_hat = torch.zeros_like(output)
                # masked_tokens = int(14*14*args.mask_ratio)*8
                # flow_hat[mask] = output[:, -masked_tokens:]
                # flow_hat[~mask] = output[:, :-masked_tokens]
                # flow_hat = flow_hat.squeeze()
                # flow_hat_reshape = rearrange(flow_hat, "(t h w) (p1 p2 c) -> c t (h p1) (w p2)", c=2, t=8, h=14, w=14, p1=16, p2=16)

                masked_tokens = int(14*14*args.mask_ratio)*8
                flow_hat_reshape = unpatchify_flow(output, mask, masked_tokens=masked_tokens)

                flows_rgb = []
                flow_hat_rgb = []
                for t in range(8):
                    flows_rgb.append(flow_to_color(flows.squeeze()[:, t, ...].cpu().numpy().transpose(1, 2, 0), convert_to_bgr=False))
                    flow_hat_rgb.append(flow_to_color(flow_hat_reshape[:, t, ...].cpu().numpy().transpose(1, 2, 0), convert_to_bgr=False))

                flows_rgb = torch.from_numpy(np.stack(flows_rgb, axis=0).transpose(0, 3, 1, 2))
                flow_hat_rgb = torch.from_numpy(np.stack(flow_hat_rgb, axis=0).transpose(0, 3, 1, 2))

                all_cat = torch.cat((flows_rgb, flow_hat_rgb), dim=0) / 255

                save_image(all_cat, f"./log/flow_vis_{j}_{i}.png")
                print(f"saved image: ./log/flow_vis_{j}_{i}.png")


            elif args.ts_pretrain:
                if len(output) == 8:
                    rgb_rgb_hat, rgb_flow_hat, flow_rgb_hat, flow_flow_hat, rgb_vis, flow_vis, rgb_token, flow_token = output
                else:
                    rgb_rgb_hat, rgb_flow_hat, flow_flow_hat, rgb_vis, flow_vis, rgb_token, flow_token = output
                    flow_rgb_hat = None

                masked_tokens = int(14*14*args.mask_ratio*8)

                unnormed_frame = frame.squeeze() * std + mean
                unnormed_frame = unnormed_frame.transpose(0, 1)

                rgb_rgb_hat_reshape = unpatchify_rgb(rgb_rgb_hat, mask, masked_tokens)
                if flow_rgb_hat is not None:
                    flow_rgb_hat_reshape = unpatchify_rgb(flow_rgb_hat, mask, masked_tokens)

                rgb_flow_hat_reshape = unpatchify_flow(rgb_flow_hat, mask, masked_tokens)
                flow_flow_hat_reshape = unpatchify_flow(flow_flow_hat, mask, masked_tokens)

                flows_rgb = []
                rgb_flow_hat_rgb = []
                flow_flow_hat_rgb = []
                for t in range(8):
                    flows_rgb.append(flow_to_color(flows.squeeze()[:, t, ...].cpu().numpy().transpose(1, 2, 0), convert_to_bgr=False))
                    rgb_flow_hat_rgb.append(flow_to_color(rgb_flow_hat_reshape[:, t, ...].cpu().numpy().transpose(1, 2, 0), convert_to_bgr=False))
                    flow_flow_hat_rgb.append(flow_to_color(flow_flow_hat_reshape[:, t, ...].cpu().numpy().transpose(1, 2, 0), convert_to_bgr=False))

                flows_rgb = torch.from_numpy(np.stack(flows_rgb, axis=0).transpose(0, 3, 1, 2))
                print(flows_rgb.shape)
                rgb_flow_hat_rgb = torch.from_numpy(np.stack(rgb_flow_hat_rgb, axis=0).transpose(0, 3, 1, 2))
                flow_flow_hat_rgb = torch.from_numpy(np.stack(flow_flow_hat_rgb, axis=0).transpose(0, 3, 1, 2))

                if flow_rgb_hat is not None:
                    all_cat = torch.cat((flows_rgb/255, rgb_flow_hat_rgb/255, flow_flow_hat_rgb/255, unnormed_frame.cpu(), flow_rgb_hat_reshape.cpu().transpose(0, 1), rgb_rgb_hat_reshape.cpu().transpose(0, 1)), dim=0)
                else:
                    all_cat = torch.cat((flows_rgb/255, rgb_flow_hat_rgb/255, flow_flow_hat_rgb/255, unnormed_frame.cpu(), rgb_rgb_hat_reshape.cpu().transpose(0, 1)), dim=0)
                save_image(all_cat, f"./log/flow_vis_{i}.png")

            elif args.flow_mode == "":
                unnormed_frame = frame.squeeze() * std + mean
                unnormed_frame = unnormed_frame.transpose(0, 1)

                # rgb_hat = output.squeeze()
                # img = torch.zeros_like(rgb_hat).unsqueeze(0)
                # img[mask] = output[:, -1408:]
                # img[~mask] = output[:, :-1408]
                # img = img.squeeze()
                # rgb_hat = rearrange(img, 'n (p c) -> n p c', c=3)
                # rgb_hat_reshape = rearrange(rgb_hat, '(t h w) (p0 p1 p2) c -> c (t p0) (h p1) (w p2)', p0=2, p1=16, p2=16, h=14, w=14)

                masked_tokens = int(14*14*args.mask_ratio)*8
                rgb_hat_reshape = unpatchify_rgb(output, mask, masked_tokens=masked_tokens)
                all_concat = torch.cat((unnormed_frame, rgb_hat_reshape.transpose(0, 1)), dim=0)

                save_image(all_concat, f"./log/flow_vis_{i}.png")
                # unnormed_rgb_hat = rgb_hat_reshape * std + mean
                # rgb_hat_reshape = rgb_hat_reshape[:, 0, ...].cpu().numpy().transpose(1, 2, 0)*255
                # cv2.imwrite(f"./log/flow_vis_{i}.png", all_concat)
                print(f"saved ./log/flow_vis_{i}.png")

            elif args.flow_mode == "online":
                pass


def warp_flow(curImg, flows):
    print(curImg.shape, flows.shape)
    h, w = flows.shape[:2]
    flows[:,:,0] += np.arange(w).astype(np.uint8)
    flows[:,:,1] += np.arange(h)[:,np.newaxis].astype(np.uint8)
    prevImg = cv2.remap(curImg.astype(np.float32), flows.astype(np.float32), None, cv2.INTER_LINEAR)

    return prevImg


def unpatchify_rgb(rgb_raw, mask, masked_tokens):
    """
        rgb_raw: torch.Tensor, (1, N, C)
        mask: torch.Tensor, (1, N)
        masked_tokens: int, number of masked tokens

        Return
        ---
        rgb_hat_reshape: reconstructed RGB image, (3, T, H, W)
    """
    # mask = mask.squeeze() # N
    img = torch.zeros_like(rgb_raw) # 1, N ,C
    img[mask] = rgb_raw[:, -masked_tokens:]
    img[~mask] = rgb_raw[:, :-masked_tokens]
    img = img.squeeze()

    rgb_hat = rearrange(img, 'n (p c) -> n p c', c=3)
    rgb_hat_reshape = rearrange(rgb_hat, '(t h w) (p0 p1 p2) c -> c (t p0) (h p1) (w p2)', p0=2, p1=16, p2=16, h=14, w=14)

    return rgb_hat_reshape

def unpatchify_flow(flow_raw, mask, masked_tokens):
    """
        flow_raw: torch.Tensor, (1, N, C)
        mask: torch.Tensor, (1, N)
        masked_tokens: int, number of masked tokens

        Return
        ---
        flow_hat_reshape: reconstructed flow image, (2, T, H, W)
    """
    flow_hat = torch.zeros_like(flow_raw)
    flow_hat[mask] = flow_raw[:, -masked_tokens:]
    flow_hat[~mask] = flow_raw[:, :-masked_tokens]
    flow_hat = flow_hat.squeeze()
    flow_hat_reshape = rearrange(flow_hat, "(t h w) (p1 p2 c) -> c t (h p1) (w p2)", c=2, t=8, h=14, w=14, p1=16, p2=16)

    return flow_hat_reshape


if __name__ == "__main__":
    opts = get_args()
    config = parse_yml(opts.config)
    if config is not None:
        opts = combine(opts, config)

    if opts.output_dir:
        Path(opts.output_dir).mkdir(parents=True, exist_ok=True)

    main(opts)
