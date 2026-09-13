from ribbn_scripts.hardware_api.hardware import *
from ribbn_scripts.ref_functions.spec_functions import *
import pandas as pd
import numpy as np
import time
from matplotlib import pyplot as plt

vna = VNA()
vna.set_pwr(-15)

tag = Tag("COM34")
save_folder="C:/git/BSTag-Firmware/data_collection/ribbn_scripts/src/ribbn_scripts/calibrations/VNA_data_Sept2026"

tag_name=tag.get_mac().replace(":",'_')
print(tag_name)

tag.reflect(2)
time.sleep(1)
pwr = -15
vna.set_pwr(pwr)
time.sleep(1)


# ch_list = [b'ch_1\0\n', b'ch_2\0\n', b'ch_3\0\n', b'ch_4\0\n', b'ch_5\0\n', b'ch_6\0\n', b'ch_7\0\n', b'ch_8\0\n']
ch_list = [1,2,3,4,5,6,7,8]

ch_ig = []

for ch in ch_list:
    if ch in ch_ig:
        continue

    tag.reflect(ch)
    print(tag.get_adc_val())
    time.sleep(1)
    vna.wtf('/automated_folder/file_name')
    f = vna.transfer_file('/automated_folder/file_name',
                          f'{save_folder}/{tag_name}_channel_' + str(ch) + '_vna_pwr_' + str(
                              -1 * pwr) + '.csv')
    print(ch)

vna.set_pwr(-30)
# C13 = '10pF'
# C17 = '3.3pF'
# L2 = '8.2nH'
# fn = C13 + '_' + C17 + '_' + L2 + '_' + str()
# arr = []
# for pwr in range(-25, -15, 1):
#     vna.set_pwr(pwr)
#     time.sleep(1)
#     vna.wtf('/automated_folder/file_name')
#     f = vna.transfer_file('/automated_folder/file_name', '../../data/calibration/vna/diode_char/' + fn + '_pwr_'+str(pwr)+'.csv')
#     v = tag1.get_adc_val()
#     if v > 0:
#         arr.append(v)
#     else:
#         arr.append(0)
#
# with open('../../data/calibration/vna/diode_char/' + fn + '.txt', 'w') as f:
#     for item in arr:
#         f.write("%s\n" % item)
# print(arr)
# vna.set_pwr(-30)
