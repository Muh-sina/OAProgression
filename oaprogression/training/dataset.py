import torch
import torch.utils.data as data
import cv2
import os
import pandas as pd
from oaprogression.kvs import GlobalKVS
from sklearn.model_selection import GroupKFold
from termcolor import colored

import matplotlib.pyplot as plt
import numpy as np

from torchvision import transforms
import solt.transforms as slt
import solt.core as slc
import solt.data as sld
import copy
from functools import partial

cv2.ocl.setUseOpenCL(False)
cv2.setNumThreads(0)


class OAProgressionDataset(data.Dataset):
    def __init__(self, dataset, split, trf):
        self.dataset = dataset
        self.split = split
        self.transforms = trf

    def __getitem__(self, ind):
        if isinstance(ind, torch.Tensor):
            ind = ind.item()
        entry = self.split.iloc[ind]
        fname = os.path.join(self.dataset, '{}_00_{}.png'.format(entry.ID, entry.Side))
        img = cv2.imread(fname, 0)
        if entry.Side == 'L':
            img = cv2.flip(img, 1)
        img = img.reshape((img.shape[0], img.shape[1], 1))
        img, kl_grade, label = self.transforms((img, entry.KL, entry.Progressor))

        res = {'KL': kl_grade,
               'img': img,
               'label': label,
               'ID_SIDE': str(entry.ID) + '_' + entry.Side
               }

        return res

    def __len__(self):
        return self.split.shape[0]


class AgeSexBMIDataset(data.Dataset):
    def __init__(self, dataset, split, trf):
        self.dataset = dataset
        self.split = split
        self.transforms = trf

    def __getitem__(self, ind):
        if isinstance(ind, torch.Tensor):
            ind = ind.item()
        entry = self.split.iloc[ind]
        fname = os.path.join(self.dataset, '{}_00_{}.png'.format(entry.ID, entry.Side))
        img = cv2.imread(fname, 0)
        if entry.Side == 'L':
            img = cv2.flip(img, 1)
        img = img.reshape((img.shape[0], img.shape[1], 1))
        img, age, sex, bmi = self.transforms((img, entry.AGE, entry.SEX, entry.BMI))

        res = {'AGE': age,
               'SEX': sex,
               'BMI': bmi,
               'img': img,
               'ID_SIDE': str(entry.ID) + '_' + entry.Side
               }

        return res

    def __len__(self):
        return self.split.shape[0]


def init_age_sex_bmi_metadata():
    kvs = GlobalKVS()

    oai_meta = pd.read_csv(os.path.join(kvs['args'].metadata_root, 'OAI_progression.csv'))
    clinical_data_oai = pd.read_csv(os.path.join(kvs['args'].metadata_root, 'OAI_participants.csv'))
    oai_meta = pd.merge(oai_meta, clinical_data_oai, on=('ID', 'Side'))
    oai_meta = oai_meta[~oai_meta.BMI.isna() & ~oai_meta.AGE.isna() & ~oai_meta.SEX.isna()]

    clinical_data_most = pd.read_csv(os.path.join(kvs['args'].metadata_root, 'MOST_participants.csv'))
    metadata_test = pd.read_csv(os.path.join(kvs['args'].metadata_root, 'MOST_progression.csv'))
    metadata_test = pd.merge(metadata_test, clinical_data_most, on=('ID', 'Side'))

    kvs.update('metadata', oai_meta)
    kvs.update('metadata_test', metadata_test)
    gkf = GroupKFold(n_splits=5)
    cv_split = [x for x in gkf.split(kvs['metadata'],
                                     kvs['metadata'][kvs['args'].target_var],
                                     kvs['metadata']['ID'].astype(str))]

    kvs.update('cv_split_all_folds', cv_split)
    kvs.save_pkl(os.path.join(kvs['args'].snapshots, kvs['snapshot_name'], 'session.pkl'))


def init_progression_metadata():
    # We get should rid of non-progressors from MOST because we can check
    # non-progressors only up to 84 months
    kvs = GlobalKVS()

    most_meta = pd.read_csv(os.path.join(kvs['args'].metadata_root, 'MOST_progression.csv'))
    oai_meta = pd.read_csv(os.path.join(kvs['args'].metadata_root, 'OAI_progression.csv'))

    kvs.update('metadata', oai_meta)
    kvs.update('metadata_test', most_meta)

    gkf = GroupKFold(n_splits=5)
    cv_split = [x for x in gkf.split(kvs['metadata'],
                                     kvs['metadata']['Progressor'],
                                     kvs['metadata']['ID'].astype(str))]

    kvs.update('cv_split_all_folds', cv_split)
    kvs.save_pkl(os.path.join(kvs['args'].snapshots, kvs['snapshot_name'], 'session.pkl'))

    print(colored("==> ", 'green') + f"Train dataset has "
                                     f"{(kvs['metadata'].Progressor == 0).sum()} non-progressed knees")

    print(colored("==> ", 'green')+f"Train dataset has "
                                   f"{(kvs['metadata'].Progressor > 0).sum()} progressed knees")

    print(colored("==> ", 'green') + f"Test dataset has "
                                     f"{(kvs['metadata_test'].Progressor == 0).sum()} non-progressed knees")

    print(colored("==> ", 'green')+f"Test dataset has "
                                   f"{(kvs['metadata_test'].Progressor > 0).sum()} progressed knees")


def img_labels2solt(inp):
    img, age, sex, bmi = inp
    return sld.DataContainer((img, age, sex, bmi), fmt='ILLL')


def unpack_solt_data(dc: sld.DataContainer):
    return dc.data


def apply_by_index(items, transform, idx=0):
    """Applies callable to certain objects in iterable using given indices.
    Parameters
    ----------
    items: tuple or list
    transform: callable
    idx: int or tuple or or list None
    Returns
    -------
    result: tuple
    """
    if idx is None:
        return items
    if not isinstance(items, (tuple, list)):
        raise TypeError
    if not isinstance(idx, (int, tuple, list)):
        raise TypeError

    if isinstance(idx, int):
        idx = [idx, ]

    idx = set(idx)
    res = []
    for i, item in enumerate(items):
        if i in idx:
            res.append(transform(item))
        else:
            res.append(copy.deepcopy(item))

    return res


def init_train_augs():
    trf = transforms.Compose([
        img_labels2solt,
        slc.Stream([
            slt.PadTransform(pad_to=(700, 700)),
            slt.CropTransform(crop_size=(700, 700), crop_mode='c'),
            slt.ResizeTransform((310, 310)),
            slt.ImageAdditiveGaussianNoise(p=0.5, gain_range=0.3),
            slt.RandomRotate(p=1, rotation_range=(-10, 10)),
            slt.CropTransform(crop_size=(300, 300), crop_mode='r'),
            slt.ImageGammaCorrection(p=0.5, gamma_range=(0.5, 1.5)),
            slt.ImageColorTransform(mode='gs2rgb')
        ], interpolation='bicubic', padding='z'),
        unpack_solt_data,
        partial(apply_by_index, transform=transforms.ToTensor(), idx=0),
    ])
    return trf


def debug_augmentations(n_iter=20):
    kvs = GlobalKVS()

    ds = OAProgressionDataset(dataset=kvs['args'].dataset_root,
                              split=kvs['metadata'],
                              transforms=init_train_augs())

    for ind in np.random.choice(len(ds), n_iter, replace=False):
        sample = ds[ind]
        img = np.clip(sample['img'].numpy()*255, 0, 255).astype(np.uint8)
        img = np.swapaxes(img, 0, -1)
        img = np.swapaxes(img, 0, 1)
        plt.figure()
        plt.imshow(img)
        plt.show()



