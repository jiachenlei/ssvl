from torchvision import transforms
from transforms import *
from masking_generator import TubeMaskingGenerator, AgnosticMaskingGenerator

from ego4d import Ego4dFhoOscc, Ego4dFhoLTA, Ego4dFhoHands, Egoclip
from epickitchens import Epickitchens


def create_mask_generator(args):
    generate_fns = {
        "agnostic": AgnosticMaskingGenerator,
        "tube": TubeMaskingGenerator,
    }

    rgb_masked_position_generator = None
    flow_masked_position_generator = None

    if getattr(args, "rgb_window_size", None):
        rgb_masked_position_generator = generate_fns[args.rgb_mask_type](
            args.rgb_window_size, args.rgb_mask_ratio
        )
    else:
        # if rgb is not used in the training, then the mask does not matter
        rgb_masked_position_generator = generate_fns[args.rgb_mask_type](
            args.flow_window_size, args.rgb_mask_ratio
        )
    if getattr(args, "flow_window_size", None):
        flow_masked_position_generator = generate_fns[args.flow_mask_type](
            args.flow_window_size, args.flow_mask_ratio
        )
    else:
        # if flow is not used in the training, then the mask does not matter
        flow_masked_position_generator = generate_fns[args.flow_mask_type](
            args.rgb_window_size, args.flow_mask_ratio
        )

    return rgb_masked_position_generator, flow_masked_position_generator


class DataAugmentationForVideoMAE(object):
    def __init__(self, args):
        self.input_mean = [0.485, 0.456, 0.406]  # IMAGENET_DEFAULT_MEAN
        self.input_std = [0.229, 0.224, 0.225]  # IMAGENET_DEFAULT_STD

        # flow image should be the same given normalized images
        # thus it do not need to be normalized
        normalize = GroupNormalize(self.input_mean, self.input_std)
        # This contains random process and is rewritten for flow image processing
        self.train_augmentation = GroupMultiScaleCrop(args.input_size, [1, .875, .75, .66])

        # accpet pil image
        self.transform = transforms.Compose([                            
            self.train_augmentation,
            Stack(roll=False),
            ToTorchFormatTensor(div=True),
            normalize,
        ])

    def __call__(self, images):
        process_data, flows_or_none = self.transform(images)
        return process_data, flows_or_none

    def __repr__(self):
        repr = "(DataAugmentationForVideoMAE,\n"
        repr += "  transform = %s,\n" % str(self.transform)
        repr += ")"
        return repr


def build_pretraining_dataset(args, **kwargs):
    
    transform = DataAugmentationForVideoMAE(args)
    mode = "train"

    if args.cfg.task == "egoclip":
        dataset = Egoclip(mode, args.cfg, pretrain=True, pretrain_transform=transform)

    elif args.cfg.task == "epic-kitchens":
        dataset = Epickitchens(args.cfg, mode, pretrain_transform=transform)

    else:
        raise NotImplementedError()

    print("Data Aug = %s" % str(transform))
    return dataset

# build finetuning dataset
def build_dataset(mode, args, flow_extractor=None):

    num_classes = -1

    if args.cfg.task == "oscc" or args.cfg.task == "pnr":
        dataset = Ego4dFhoOscc(mode, args.cfg, pretrain=False, flow_extractor=flow_extractor)
        num_classes = 2 if args.cfg.task == "oscc" else 32

    elif "lta" in args.cfg.task: # [lta_verb, lta_noun]
        dataset = Ego4dFhoLTA(mode, args.cfg, pretrain=False, flow_extractor=flow_extractor)
        # some verb or noun class does not exist in training set, e.g 29
        num_classes = 115 if args.cfg.task == "lta_verb" else 478

    elif args.cfg.task == "hands":
        dataset = Ego4dFhoHands(mode, args.cfg, pretrain=False, flow_extractor=flow_extractor)

    elif "egoclip" in args.cfg.task:
        dataset = Egoclip(mode, args.cfg, pretrain=False, flow_extractor=flow_extractor)        
        num_classes = 118 if args.cfg.task == "egoclip_verb" else 582

    else:
        raise NotImplementedError()


    return dataset, num_classes
