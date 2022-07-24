import logging
import numpy as np
import time
import torch
from PIL import Image
import cv2
import os

logger = logging.getLogger(__name__)

import time
import zipfile
from zipfile import ZipFile
import tarfile
import shutil

from multiprocessing import Lock
from collections import defaultdict

class CacheManager(object):

    def __init__(self, log_path="./"):
        self.log_path = log_path
        self.lock_dct = defaultdict(Lock)
        self.check_dct = dict()

    def exists_auto_acquire(self, path):
        if path in self.lock_dct.keys():
            if path in self.check_dct.keys():
                return True
            else:
                self.lock_dct[path].acquire()
                self.lock_dct[path].release()
                return True
        else:
            # if not exists, then lock until finish caching
            self.lock_dct[path].acquire()
            return False

    def acquire(self, path):
        self.lock_dct[path].acquire()

    def release_and_check(self, path):
        self.check_dct[path] = True
        with open(os.path.join(self.log_path,"cache.log"), "a+") as logf:
            logf.write(path+"\n")
        self.lock_dct[path].release()


def cache_tar_to_local(zip_file_path, raw_dest, cache_log_file = "cache.log", flow=False, cache_manager=None):

    assert os.path.exists(zip_file_path), "Zip file not found when caching it locally"
    zip_file_name = zip_file_path.split("/")[-1]

    # if already cached, then return
    dest = os.path.join(raw_dest, "flow" if flow else "rgb")
    assert cache_manager is not None, "Cache mananger is None"
    if cache_manager.exists_auto_acquire(os.path.join(dest, zip_file_name)):
        return True

    # else copy file and handle potential error
    os.makedirs(dest, exist_ok=True)
    
    retry = 10
    for i in range(retry):
        # keep trying caching tar file
        try:
            ret_dest = shutil.copy(zip_file_path, dest)
            # write to cache log file
            # cache_log_fbar = open(cache_log_file, "a+")
            # cache_log_fbar.write(os.path.join(dest, zip_file_name) + "\n")
            # cache_log_fbar.close()
            cache_manager.release_and_check(os.path.join(dest, zip_file_name))
            return True

        except OSError as e:
            logger.warn(f"Caching tar file to local directory failed:\nRaw Exception:\n{e}")
            cache_manager.release_and_check(os.path.join(dest, zip_file_name))
            return False

            # assume not enough space and delete pre-cached tar file
            # cache_log_fbar = open(cache_log_file, "r")
            # # ATTENTION: with \n at tail of each element in the list
            # # each element in the list is a absolute path of previously cached zip file
            # cached_file_lst = cache_log_fbar.readlines()
            # cache_log_fbar.close()

            # if len(cached_file_lst) != 0:

            #     zip_file_path = cached_file_lst[0].strip("\n")
            #     cached_file_lst.pop(0)

            #     cache_log_fbar = open(cache_log_file, "w")
            #     cache_log_fbar.write("".join(cached_file_lst))
            #     cache_log_fbar.close()
            # else:
            #     return False

            # # remove earliest cached file
            # try:
            #     os.remove(zip_file_path)
            # except:
            #     print(f"Fail to delete cached file:{zip_file_path}, continue removing next tar files...")
            #     continue

            # print(f"Deleted previously cached file:{zip_file_path} and try again...")

        except Exception as e:
            cache_manager.release_and_check(os.path.join(dest, zip_file_name))
            logger.warn(f"Caching tar file to local directory failed:\nRaw Exception:\n{e}")
            return False

    logger.warn(f"Reach maximum caching attempts... zip_file_path:{zip_file_path}")

def extract_zip(path_to_save, ext="tar", frame_list = [], flow=False, cache_dest="/data/jiachen/temp", cache_manager=None, force=False):

    # num_frames = len(os.listdir(path_to_save)) # existing frames in the directory
    message = f"Zip file does not exists: {path_to_save}"
    assert os.path.exists(path_to_save + "." + ext), message
    os.makedirs(path_to_save, exist_ok=True)

    logger.info(f"Start extracting frame from zip file:{path_to_save}.{ext} ...")
    start_time = time.time()

    # if ext == "zip":
    #     try:
    #         zf = ZipFile( path_to_save + "." + ext, "r")
    #     except zipfile.BadZipFile:       
    #         raise Exception(f"Exception occurs while opening zip file: {path_to_save}.zip, file might be corrupted")

    #     if len(frame_lst) != 0:
    #         namelist = zf.get
    #         for frame in frame_lst:
                
    #     else:
    #         zf.extractall(path_to_save)
    #         zf.close()

    if ext == "tar":
        try:
            if len(frame_list) != 0:

                if cache_dest == "":
                    # specify where to cache compressed file
                    cache_dest = os.getcwd()
                # if only extract several frames from the tar file then to ensure reading efficiency
                # cache tar file locally
                ret = cache_tar_to_local(path_to_save + "." + ext, raw_dest=cache_dest, flow=flow, cache_manager=cache_manager)
                # print(f"caching file return: {ret}")
                if ret:
                    zip_file_name = path_to_save.split("/")[-1] + "." + ext
                    # read from local directory
                    tf = tarfile.open( os.path.join(cache_dest, "flow" if flow else "rgb", zip_file_name), "r")
                    # print("opened local compressed file")
                else:
                    # fail to cache tar file, read from original path
                    tf = tarfile.open( path_to_save + "." + ext, "r")
            else:
                tf = tarfile.open( path_to_save + "." + ext, "r")

        except Exception as e:
            raise Exception(f"Exception occurs while opening tar file: {path_to_save}.tar, file might be corrupted \
                            \rRaw exception:\n{e}")

        if len(frame_list) != 0:
            dir_name = path_to_save.split("/")[-1]
            retry = 5         
            if flow:
                # Obtain existing flow image list to prevent duplicate writing
                if os.path.exists(os.path.join(path_to_save, "u")):
                    exist_uflow_list = os.listdir(os.path.join(path_to_save, "u"))
                else:
                    exist_uflow_list = []
                if os.path.exists(os.path.join(path_to_save, "v")):
                    exist_vflow_list = os.listdir(os.path.join(path_to_save, "v"))
                else:
                    exist_vflow_list= []

                for frame_idx in frame_list:
                    for i in range(retry):
                        try:
                            if not frame_idx in exist_uflow_list or force:
                                tf.extract(f"./u/{frame_idx}", path_to_save)
                            if not frame_idx in exist_vflow_list or force:
                                tf.extract(f"./v/{frame_idx}", path_to_save)
                            break
                        except KeyError as e:
                            raise Exception(f"Key error raised tf.names:{tf.getnames()[:20]}... frame_idx:{frame_idx} frame_list:{frame_list} path_to_save:{path_to_save}")
                        except FileExistsError as e:
                            logger.warn(f"When extracting {path_to_save} {frame_idx}, file eixsts, retrying...")
                            continue
            else:
                if os.path.exists(path_to_save):
                    exist_frame_list = os.listdir(path_to_save)
                else:
                    exist_frame_list = []

                for frame_idx in frame_list:
                    for i in range(retry):
                        try:
                            if not frame_idx in exist_frame_list or force:
                                tf.extract("./"+frame_idx, path_to_save)
                            break
                        except KeyError as e:
                            raise Exception(f"Key error raised tf.names:{tf.getnames()[:50]}... frame_idx:{frame_idx} frame_list:{frame_list} path_to_save:{path_to_save}")
                        except FileExistsError as e:
                            logger.warn(f"When extracting {path_to_save} {frame_idx}, file eixsts, retrying...")
                            continue
        else:
            tf.extractall(path_to_save)

        tf.close()
    else:
        raise ValueError(f"Unsupported compressed file type: {ext}, expect one of [zip, tar]")

    end_time = time.time()
    logger.info(f"Finish processing zipfile {path_to_save}, time taken: {end_time-start_time}")


def retry_load_images(image_paths, retry=10, backend="pytorch", as_pil=False, path_to_compressed="", online_extracting=False, flow=False, video_record=None, cache_manager=None):
    """
    This function is to load images with support of retrying for failed load.
    Args:
        image_paths (list): paths of images needed to be loaded.
        retry (int, optional): maximum time of loading retrying. Defaults to 10.
        backend (str): `pytorch` or `cv2`.
    Returns:
        imgs (list): list of loaded images.
    """

    for image_path in image_paths:
        try:
            Image.open(image_path)
        except:
        # if image does not exist or is corrupted will raise an error
        # we assume the file is missing instead of corrupted here
        # we will handle corruption latter if any
            assert os.path.exists(path_to_compressed), f"image file {image_paths} not exists while compressed file does not exist: {path_to_compressed}"
            if online_extracting:
                img_tmpl = "frame_{:010d}.jpg"

                if video_record is None:
                    # flst = [image_path.split("/")[-1] for image_path in image_paths]

                    st = image_paths[0].split("_")[-1].split(".")[0]
                    end = image_paths[-1].split("_")[-1].split(".")[0]
                    flst = [ img_tmpl.format(idx) for idx in range(int(st), int(end)+1, 1) ]

                else:
                    st = int(video_record.start_frame)
                    n = int(video_record.num_frames)
                    if flow:
                        if st % 2 == 0:
                            st += 1
                        flst = [img_tmpl.format(idx//2 + 1) for idx in range(st, st+n+1, 2)]
                    else:
                        flst = [img_tmpl.format(idx) for idx in range(st, st+n+1, 1)]

                extract_zip(path_to_compressed, frame_list=flst, flow=flow, cache_manager=cache_manager)
            else:
                extract_zip(path_to_compressed)

            break

    for i in range(retry):
        # edited by jiachen, read image and convert to RGB format
        
        imgs = []
        for image_path in image_paths:
            try:
                if not as_pil:
                    imgs.append(cv2.cvtColor(cv2.imread(image_path), cv2.COLOR_BGR2RGB))
                else:
                    imgs.append(Image.open(image_path))

            except Exception as e:
                logger.warn(f"PIL reading error:{image_path}, extracting image file again.\nRaw exception:{e}")
                assert os.path.exists(path_to_compressed), f"image file {image_paths} not exists while compressed file does not exist: {path_to_compressed}"
                if online_extracting:
                    # flst = [image_path.split("/")[-1] for image_path in image_paths]
                    extract_zip(path_to_compressed, frame_list=[image_path.split("/")[-1]], flow=flow, cache_manager=cache_manager, force=True)
                else:
                    extract_zip(path_to_compressed)

                # break inner image reading loop and read from start
                break
 
        if len(imgs) == len(image_paths) and all(img is not None for img in imgs):
            if (as_pil == False ) and backend == "pytorch":
                imgs = torch.as_tensor(np.stack(imgs))
            return imgs

        if i == retry - 1:
            raise Exception("Failed to load images {}".format(image_paths))


def get_sequence(center_idx, half_len, sample_rate, num_frames):
    """
    Sample frames among the corresponding clip.
    Args:
        center_idx (int): center frame idx for current clip
        half_len (int): half of the clip length
        sample_rate (int): sampling rate for sampling frames inside of the clip
        num_frames (int): number of expected sampled frames
    Returns:
        seq (list): list of indexes of sampled frames in this clip.
    """
    seq = list(range(center_idx - half_len, center_idx + half_len, sample_rate))

    for seq_idx in range(len(seq)):
        if seq[seq_idx] < 0:
            seq[seq_idx] = 0
        elif seq[seq_idx] >= num_frames:
            seq[seq_idx] = num_frames - 1
    return seq

def pack_pathway_output(cfg, frames):
    """
    Prepare output as a list of tensors. Each tensor corresponding to a
    unique pathway.
    Args:
        frames (tensor): frames of images sampled from the video. The
            dimension is `channel` x `num frames` x `height` x `width`.
    Returns:
        frame_list (list): list of tensors with the dimension of
            `channel` x `num frames` x `height` x `width`.
    """
    if cfg.MODEL.ARCH in cfg.MODEL.SINGLE_PATHWAY_ARCH:
        frame_list = [frames]
    elif cfg.MODEL.ARCH in cfg.MODEL.MULTI_PATHWAY_ARCH:
        fast_pathway = frames
        # Perform temporal sampling from the fast pathway.
        slow_pathway = torch.index_select(
            frames,
            1,
            torch.linspace(
                0, frames.shape[1] - 1, frames.shape[1] // cfg.SLOWFAST.ALPHA
            ).long(),
        )
        frame_list = [slow_pathway, fast_pathway]
    else:
        raise NotImplementedError(
            "Model arch {} is not in {}".format(
                cfg.MODEL.ARCH,
                cfg.MODEL.SINGLE_PATHWAY_ARCH + cfg.MODEL.MULTI_PATHWAY_ARCH,
            )
        )
    return frame_list