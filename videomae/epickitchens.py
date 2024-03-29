import os
import csv
import pandas as pd
import torch
import torch.utils.data
import torch.nn.functional as F
import random
import numpy as np
import logging

from PIL import Image

import video_transforms as transform
import epickitchens_utils as utils
from epickitchens_record import EpicKitchensVideoRecord

from timm.data.constants import IMAGENET_DEFAULT_MEAN, IMAGENET_DEFAULT_STD

logger = logging.getLogger(__name__)


def get_start_end_idx(video_size, clip_size):
    """
    Sample a clip of size clip_size from a video of size video_size and
    return the indices of the first and last frame of the clip. If clip_idx is
    -1, the clip is randomly sampled, otherwise uniformly split the video to
    num_clips clips, and select the start and end index of clip_idx-th video
    clip.
    Args:
        video_size (int): number of overall frames.
        clip_size (int): size of the clip to sample from the frames.
        clip_idx (int): if clip_idx is -1, perform random jitter sampling. If
            clip_idx is larger than -1, uniformly split the video to num_clips
            clips, and select the start and end index of the clip_idx-th video
            clip.
        num_clips (int): overall number of clips to uniformly sample from the
            given video for testing.

        flow_pretrain (bool): whether pretraining and predicting flow images
        random_strategy (bool): whether sample clip of random size this will lead to no adjascent frames will be sampled

    Returns:
        start_idx (int): the start frame index.
        end_idx (int): the end frame index.
    """


    delta = max(video_size - clip_size, 0)

    start_idx = random.uniform(0, delta)

    end_idx = start_idx + clip_size - 1
    return start_idx, end_idx


def temporal_sampling(num_frames, start_idx, end_idx, num_samples, start_frame=0):
    """
    Given the start and end frame index, sample num_samples frames between
    the start and end with equal interval.
    Args:
        num_frames (int): number of frames of the trimmed action clip
        start_idx (int): the index of the start frame.
        end_idx (int): the index of the end frame.
        num_samples (int): number of frames to sample.
        start_frame (int): starting frame of the action clip in the untrimmed video

        flow_pretrain (bool): whether predicting flow images at pretraining
    Returns:
        frames (tersor): a tensor of temporal sampled video frames, dimension is
            `num clip frames` x `channel` x `height` x `width`.
    """
    # NOTE
    # 1. num_samples is an even number
    # 2. start_idx is a odd number
    # 3. start_idx and end_idx returned by get_start_end_idx() are increased by one

    assert num_samples % 2 == 0, f"number of frames:{num_samples} to be sampled should be even"
    # assert start_idx % 2 == 1, f"start index:{start_idx} is not an odd number"
    raw_index = start_frame + torch.linspace(start_idx, end_idx, num_samples//2).long()

    index = [ ]
    rep_flag = False
    for i in range(len(raw_index)):
        if raw_index[i] % 2 != 0:
            if raw_index[i] + 1 < start_frame + num_frames:
                index.append(raw_index[i])
                index.append(raw_index[i] + 1)
            else:
                # replicate last frames
                rep_flag = True
                logger.debug("Replicating last two frames")
                index.extend(index[-2:])
        else:
            if raw_index[i] < start_frame + num_frames:
                index.append(raw_index[i] - 1)
                index.append(raw_index[i])
            else:
                rep_flag = True
                logger.debug("Replicating last two frames")
                index.extend(index[-2:])

    index = torch.clamp( torch.as_tensor(index), start_frame, start_frame + num_frames - 1)

    if rep_flag:
        logger.debug(f"{raw_index}, {index}, {start_frame}, {start_frame + num_frames}, {num_frames}")

    return index



def pack_frames_to_video_clip(cfg, video_record, target_fps=60,
                            as_pil = False,
                            mode = "train",
                            cache_manager= None,
                            ):

    """
        ...

        as_pil (bool): whether return frames as pil image
        flow_mode (str): work with use_preprocessed_flow, indicates different flow image sampling strategy
        flow_pretrain (bool): whether predicting flow images at pretraining
    """

    if cfg.VERSION == 100:
        # if is epic-kitchen 100
        path_to_video = '{}/{}/rgb_frames/{}'.format(cfg.EPICKITCHENS.VISUAL_DATA_DIR,
                                                    video_record.participant,
                                                    video_record.untrimmed_video_name)

        path_to_flow = '{}/{}/flow_frames/{}'.format(cfg.EPICKITCHENS.VISUAL_DATA_DIR,
                                                    video_record.participant,
                                                    video_record.untrimmed_video_name)

    elif cfg.VERSION == 55:
        message = f"Unkown split:{mode} for Epic-Kitchen55, expect one of [train/test]"
        assert mode in ["train", "test"], message
        # else if epic-kitchen 55
        # Load video by loading its extracted frames
        path_to_video = '{}/rgb/{}/{}/{}'.format(cfg.EPICKITCHENS.VISUAL_DATA_DIR,
                                                    mode,
                                                    video_record.participant,
                                                    video_record.untrimmed_video_name)
        path_to_flow = '{}/flow/{}/{}/{}'.format(cfg.EPICKITCHENS.VISUAL_DATA_DIR,
                                                    mode,
                                                    video_record.participant,
                                                    video_record.untrimmed_video_name)

    else:
        raise ValueError(f"Unknwon Epic-kitchen version: {cfg.VERSION}")


    img_tmpl = "frame_{:010d}.jpg"
    fps, sampling_rate, num_samples = video_record.fps, cfg.DATA.SAMPLING_RATE, cfg.DATA.NUM_FRAMES


    # indicates that we are pretrainning on Epic-kitchen by predicting flow images
    assert num_samples % 2 == 0, \
        "When pretraining on Epic-kitchen and predicting flow images, number of sampled frames should be even number"

    start_idx, end_idx = get_start_end_idx(
        video_record.num_frames,
        num_samples * sampling_rate * fps / target_fps,
    )

    start_idx, end_idx = start_idx + 1, end_idx + 1
    frame_idx = temporal_sampling(video_record.num_frames,
                                  start_idx, end_idx, num_samples,
                                  start_frame = video_record.start_frame,
                                  )

    if getattr(cfg.DATA, "READ_FROM_TAR", None):
        source = path_to_video
        name = f"{video_record.untrimmed_video_name}_{video_record._index}"
        frames = utils.read_from_tarfile(source, name, frame_idx, as_pil=as_pil, flow=False)

        source = path_to_flow
        uflows, vflows = utils.read_from_tarfile(source, name, frame_idx, as_pil=as_pil, flow=True)
        return frames, uflows, vflows

    elif getattr(cfg.DATA, "READ_FROM_ZIP", None):

        source = path_to_video
        name = f"{video_record.untrimmed_video_name}_{video_record._index}"
        frames = utils.read_from_zip_file(source, name, frame_idx, as_pil=as_pil, flow=False)

        source = path_to_flow
        uflows, vflows = utils.read_from_zip_file(source, name, frame_idx, as_pil=as_pil, flow=True)
        return frames, uflows, vflows

    img_paths = [os.path.join(path_to_video, img_tmpl.format(idx.item())) for idx in frame_idx]

    # code below will extract frames from compressed file [tar, ] if the directory not exist
    if not os.path.isdir(path_to_video):
        if cfg.ONINE_EXTRACTING:
            st = video_record.start_frame
            n = video_record.num_frames
            frame_list = [img_tmpl.format(idx) for idx in range(st, st+n+1)]
            utils.extract_zip(path_to_video, frame_list=frame_list, cache_manager=cache_manager)
        else:
            utils.extract_zip(path_to_video)

    frames = utils.retry_load_images(img_paths, as_pil=as_pil, path_to_compressed = path_to_video, online_extracting=cfg.ONINE_EXTRACTING, video_record=None, cache_manager=cache_manager)

    # NOTE
    # idx range in [1, video frames]

    # use pre-extracted flow images in epic-kitchens55

    # NOTE
    # sample strategy:
    # frame of odd index:  sample flow between it and its next frame
    # frame of even index: sample flow between its next frames

    # when using this strategy last frame of a video should not be sampled
    # since corresponding flow image might not exist

    u_flow_paths = []
    v_flow_paths = []
    # _debug_frame_idx = []
    for i in range(0, len(frame_idx), 2):

        idx  = frame_idx[i]
        # _debug_frame_idx.append(idx.item()//2 + 1)

        assert idx % 2 == 1, f"idx:{idx} should be an odd number. video_record.start_frame:{video_record.start_frame} path_to_video:{path_to_video}, frame_idx:{frame_idx}. {start_idx}, {end_idx}, untrimmed_video_name:{video_record.untrimmed_video_name}, {video_record.num_frames}"

        upath = os.path.join(path_to_flow, "u", img_tmpl.format( idx.item()//2 + 1))
        vpath = os.path.join(path_to_flow, "v", img_tmpl.format( idx.item()//2 + 1))

        # if not os.path.exists(upath) or not os.path.exists(vpath):
        #     # if we sampled last frame of a video that corresponding flow or flow image of its subsequent frames does not exist
        #     # then this should be the last iteration and this frame should be the last frame in the sampled frame list
        #     # we can simply drop this flow and do not compute the loss of corresponding predicted flow image

        #     assert i == len(frame_idx) - 1, f"Corresponding flow image of this frame or flow image of its subsequent frames does not exist\n and this frame is not the last sampled frame:\n {path_to_video}: {idx}"

        #     print(f"Warning: found a none existent flow image: {path_to_flow} {upath.split('/')[-1]}")
        #     print(" last predicted flow image will not be computed in loss")

        u_flow_paths.append(upath)
        v_flow_paths.append(vpath)

    # print(f"path_to_video: {path_to_video} Sampled rgb image: {frame_idx} Sampled flow image: {_debug_frame_idx}")

    if not os.path.isdir(path_to_flow):
        if cfg.ONINE_EXTRACTING:
            st = video_record.start_frame if video_record.start_frame%2 == 1 else video_record.start_frame+1
            n = video_record.num_frames
            frame_list = [img_tmpl.format(idx//2 + 1) for idx in range(st, st+n+1, 2)]
            utils.extract_zip(path_to_flow, frame_list=frame_list, flow=True, cache_manager=cache_manager)
        else:
            utils.extract_zip(path_to_flow)

    uflows = utils.retry_load_images(u_flow_paths, as_pil=True, path_to_compressed= path_to_flow, online_extracting=cfg.ONINE_EXTRACTING, flow=True, video_record=None, cache_manager=cache_manager)
    vflows = utils.retry_load_images(v_flow_paths, as_pil=True, path_to_compressed= path_to_flow, online_extracting=cfg.ONINE_EXTRACTING, flow=True, video_record=None, cache_manager=cache_manager)

    # print(np.array(uflows[0])[:10,:10])
    return frames, uflows, vflows

    # elif flow_mode == "online":
    #     # extract flow online

    #     pass


"""
Used Configuration:

# general
EPICKITCHENS.ANNOTATIONS_DIR    path to directory that contains annotation file
EPICKITCHENS.VISUAL_DATA_DIR    path to directory that contains data of different participants
EPICKITCHENS.TRAIN_LIST         path to pickle file that contains information of training data
EPICKITCHENS.VAL_LIST           ...
EPICKITCHENS.TEST_LIST          ...
DATA.MEAN                       float that represents mean used to normalize dataset
DATA.STD                        float that represents std used to normalize dataset
DATA.SAMPLING_RATE              int 
DATA.NUM_FRAMES                 int 

# train, val, trian+val
DATA.TRAIN_JITTER_SCALES
DATA.TRAIN_CROP_SIZE

# test
TEST.NUM_SPATIAL_CROPS
TEST.NUM_ENSEMBLE_VIEWS

DATA.TEST_CROP_SIZE

# slowfast
MODEL.ARCH
MODEL.SINGLE_PATHWAY_ARCH
MODEL.MULTI_PATHWAY_ARCH

"""

class Epickitchens(torch.utils.data.Dataset):

    def __init__(self, cfg, mode, pretrain_transform=None,
                 cache_manager=None, flow_extractor=None):

        assert mode in [
            "train",
            "val",
            "test",
            "train+val"
        ], "Split '{}' not supported for EPIC-KITCHENS".format(mode)
        self.cfg = cfg
        self.mode = mode

        self.pretrain_transform = pretrain_transform  # data transformation for pretraining

        self.cache_manager = cache_manager
        self.flowExt = flow_extractor

        self.target_fps = 60
        # For training or validation mode, one single clip is sampled from every
        # video. For testing, NUM_ENSEMBLE_VIEWS clips are sampled from every
        # video. For every clip, NUM_SPATIAL_CROPS is cropped spatially from
        # the frames.
        if self.mode in ["train", "train+val"]:
            self._num_clips = 1

        logger.info("Constructing EPIC-KITCHENS {}...".format(mode))
        self._construct_loader()

    def _construct_loader(self):
        """
        Construct the video loader.
        """
        if self.mode == "train":
            path_annotations_pickle = [os.path.join(self.cfg.EPICKITCHENS.ANNOTATIONS_DIR, self.cfg.EPICKITCHENS.TRAIN_LIST)]
        else:
            # train and val
            path_annotations_pickle = [os.path.join(self.cfg.EPICKITCHENS.ANNOTATIONS_DIR, file)
                                       for file in [self.cfg.EPICKITCHENS.TRAIN_LIST, self.cfg.EPICKITCHENS.VAL_LIST]]

        for file in path_annotations_pickle:
            assert os.path.exists(file), "{} dir not found".format(
                file
            )
        self._video_records = []
        self._spatial_temporal_idx = []
        for file in path_annotations_pickle:
            if "csv" in file:
                reader = csv.reader(open(file, "r"))
                for row in reader:
                    tup = [row[0], 
                    {
                        "participant_id": row[1],
                        "video_id": row[2],
                        "start_timestamp": "00:" + row[4],
                        "stop_timestamp": "00:" + row[5],
                        "verb_class": row[10],
                        "noun_class": row[12],
                    }]
                    for idx in range(self._num_clips):
                        self._video_records.append(EpicKitchensVideoRecord(tup))
                        self._spatial_temporal_idx.append(idx)

            elif "pkl" in file:
                for tup in pd.read_pickle(file).iterrows():
                    for idx in range(self._num_clips):
                        self._video_records.append(EpicKitchensVideoRecord(tup))
                        self._spatial_temporal_idx.append(idx)

        assert (
                len(self._video_records) > 0
        ), "Failed to load EPIC-KITCHENS split {} from {}".format(
            self.mode, path_annotations_pickle
        )
        logger.info(
            "Constructing epickitchens dataloader (size: {}) from {}".format(
                len(self._video_records), path_annotations_pickle
            )
        )

    def __getitem__(self, index):
        """
        Given the video index, return the list of frames, label, and video
        index if the video can be fetched and decoded successfully, otherwise
        repeatly find a random video that can be decoded as a replacement.
        Args:
            index (int): the video index provided by the pytorch sampler.
        Returns:
            frames (tensor): the frames of sampled from the video. The dimension
                is `channel` x `num frames` x `height` x `width`.
            label (int): the label of the current video.
            index (int): if the video provided by pytorch sampler can be
                decoded, then return the index of the video. If not, return the
                index of the video replacement that can be decoded.
        """
        if self.mode not in ["train", "train+val"]:

            raise NotImplementedError(
                "Does not support {} mode".format(self.mode)
            )

            # -1 indicates random sampling.
            # temporal_sample_index = -1
            # spatial_sample_index = -1
            # min_scale = self.cfg.DATA.TRAIN_JITTER_SCALES[0]
            # max_scale = self.cfg.DATA.TRAIN_JITTER_SCALES[1]
            # crop_size = self.cfg.DATA.TRAIN_CROP_SIZE
        # else:
        #     raise NotImplementedError(
        #         "Does not support {} mode".format(self.mode)
        #     )

        data = pack_frames_to_video_clip(
            self.cfg, self._video_records[index], 
            as_pil=True, mode=self.mode, cache_manager=self.cache_manager
        )

        frames, *flows = data  # list of pil, [list of pil, list of pil]
        frames, flows = self.pretrain_transform((frames, flows)) # frames shape: C*T, H, W
        try:
            frames = frames.view((self.cfg.DATA.NUM_FRAMES, 3) + frames.size()[-2:]).transpose(0,1) # 3, num_frames, H, W
        except Exception as e:
            print(self._video_records[index])
            print(frames.size())
            print(e)
        # flows are processed in pretrain_transform
        # flows = flows.view((self.cfg.DATA.NUM_FRAMES, 2) + frames.size()[1:3]).transpose(0,1) # 2, num_flows, H, W
        # else:
        #     frames, mask = self.pretrain_transform((frames, None), flow_mode=self.flow_mode)
        #     frames = frames.view((self.cfg.DATA.NUM_FRAMES, 3) + frames.size()[-2:]).transpose(0,1) 

        #     if self.flow_mode == "online":
        #         # denormalize frames
        #         mean = torch.as_tensor(IMAGENET_DEFAULT_MEAN)[:, None, None, None]
        #         std = torch.as_tensor(IMAGENET_DEFAULT_STD)[:, None, None, None]

        #         # print(mean.shape)
        #         unnormed_frames =  frames * std + mean # c, t, h, w
        #         unnormed_frames = unnormed_frames.transpose(0, 1)
        #         shift_unnomred_frames = torch.roll(unnormed_frames, -1, 0)
        #         # print(f"unnormed: {unnormed_frames.shape} raw: {frames.shape}")
        #         concat_frames = torch.cat((unnormed_frames, shift_unnomred_frames), dim=1)
        #         # print(f"shift_unnomred_frames: {shift_unnomred_frames.shape}, concat: {concat_frames.shape}")
        #         concat_frames = F.pad(concat_frames, (16, 16, 16, 16), "constant", 0)
        #         # print(f"padded concat:{concat_frames.shape}")

        #         flow_lst_dct = self.flowExt.ext(concat_frames)

        #         flows = np.stack([flow_dict["flow"] for flow_dict in flow_lst_dct], axis=0)
        #         T, H, W, C = flows.shape
        #         flows = flows[:, 32:257, 32:257, :].transpose(3, 0, 1, 2)
        #         flows = torch.from_numpy(flows)
        #         # print(f"flow: {flows.shape}")

        label = self._video_records[index].label
        # commented by jiachen, if use slowfast network, then uncomment this line
        # frames = utils.pack_pathway_output(self.cfg, frames)
        metadata = self._video_records[index].metadata

        if self.cfg.DATA.REPEATED_SAMPLING > 0:
            frames = [frames for i in range(int(self.cfg.DATA.REPEATED_SAMPLING))]
            flows = [flows for i in range(int(self.cfg.DATA.REPEATED_SAMPLING))]

            frames = torch.stack(frames, dim=0)
            flows = torch.stack(flows, dim=0)

        # print(frames.shape, mask.shape, flows.shape)
        # is pretrain, then
        # if self.flow_mode == "":
        #     # if do not use flow images
        #     return frames, mask, label, index, metadata
        # else:
        return frames, flows, label, index, metadata

    def __len__(self):
        return len(self._video_records)

    # def spatial_sampling(
    #         self,
    #         frames,
    #         spatial_idx=-1,
    #         min_scale=256,
    #         max_scale=320,
    #         crop_size=224,
    # ):
    #     """
    #     Perform spatial sampling on the given video frames. If spatial_idx is
    #     -1, perform random scale, random crop, and random flip on the given
    #     frames. If spatial_idx is 0, 1, or 2, perform spatial uniform sampling
    #     with the given spatial_idx.
    #     Args:
    #         frames (tensor): frames of images sampled from the video. The
    #             dimension is `num frames` x `height` x `width` x `channel`.
    #         spatial_idx (int): if -1, perform random spatial sampling. If 0, 1,
    #             or 2, perform left, center, right crop if width is larger than
    #             height, and perform top, center, buttom crop if height is larger
    #             than width.
    #         min_scale (int): the minimal size of scaling.
    #         max_scale (int): the maximal size of scaling.
    #         crop_size (int): the size of height and width used to crop the
    #             frames.
    #     Returns:
    #         frames (tensor): spatially sampled frames.
    #     """
    #     assert spatial_idx in [-1, 0, 1, 2]
    #     if spatial_idx == -1:
    #         frames, _ = transform.random_short_side_scale_jitter(
    #             frames, min_scale, max_scale
    #         )
    #         frames, _ = transform.random_crop(frames, crop_size)
    #         frames, _ = transform.horizontal_flip(0.5, frames)
    #     else:
    #         # The testing is deterministic and no jitter should be performed.
    #         # min_scale, max_scale, and crop_size are expect to be the same.
    #         assert len({min_scale, max_scale, crop_size}) == 1
    #         frames, _ = transform.random_short_side_scale_jitter(
    #             frames, min_scale, max_scale
    #         )
    #         frames, _ = transform.uniform_crop(frames, crop_size, spatial_idx)
    #     return frames

    
# if __name__ == "__main__":
    # from datasets import DataAugmentationForVideoMAE
    # from argparse import Namespace
    # from torch.utils.data import DataLoader

    # cfg = {
    #     "EPICKITCHENS":Namespace(**{
    #     # epic-kitchen100: path to directory that contains each participant's data
    #     # epic-kitchen50: path to directory that contains two directories: flow and rgb
    #     # VISUAL_DATA_DIR: "/data/jiachen/partial-epic-kitchens55/frames_rgb_flow"
    #         "VISUAL_DATA_DIR": "/data/shared/ssvl/epic-kitchens50/3h91syskeag572hl6tvuovwv4d/frames_rgb_flow",
    #         # path to annotation file
    #         # ANNOTATIONS_DIR: "/data/jiachen/partial-epic-kitchens55/annotations"
    #         "ANNOTATIONS_DIR": "/data/shared/ssvl/epic-kitchens50/3h91syskeag572hl6tvuovwv4d/annotations",
    #         # annotation file name of train/val/test data
    #         "TRAIN_LIST": "EPIC_train_action_labels.pkl",
    #         "VAL_LIST": "",
    #         "TEST_LIST": "",
    #     }),
    #     "DATA":Namespace(**{
    #         # do not need in pretrain
    #         # - MEAN: ""
    #         # - STD: ""
    #         "SAMPLING_RATE": 2,
    #         "NUM_FRAMES": 16,
    #         "TRAIN_JITTER_SCALES": [256, 320],
    #         "TRAIN_CROP_SIZE": 224,
    #         "TEST_CROP_SIZE": 224,

    #         "REPEATED_SAMPLING": 4,
    #     }),
    #     "TEST":Namespace(**{
    #         "NUM_SPATIAL_CROPS": "",
    #         "NUM_ENSEMBLE_VIEWS": "",
    #     }),
    #     "VERSION": 55,
    #     "ONINE_EXTRACTING": True
        
    # }
    # args = {
    #     "input_size": 224,
    #     "mask_type": "agnostic",
    #     "window_size": (8, 14, 14),
    #     "mask_ratio": 0.9,
    # }
    # cfg = Namespace(**cfg)
    # args = Namespace(**args)

    # flow_mode = "local"

    # transform = DataAugmentationForVideoMAE(args=args, flow_mode = flow_mode)
    # dataset = Epickitchens(cfg, "train",
    #              pretrain=True,  pretrain_transform=transform,  flow_mode = flow_mode,)
    
    # loader = DataLoader(
    #     dataset,
    #     num_workers= 5,
    #     batch_size=8,
    # )
    
    # for batch in loader:
    #     frame, mask, flows = batch[:3]
    #     print(frame.shape, mask.shape, flows.shape)