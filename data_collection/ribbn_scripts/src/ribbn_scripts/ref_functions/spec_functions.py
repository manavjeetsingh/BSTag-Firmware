import pandas as pd
import numpy as np
import math


# sets switch state on tag
def set_ch(tag, ch, switch_configurations):
    row = switch_configurations[switch_configurations['Ch'] == ch]
    # print(row)
    tag.reflect(bytes(str(int(row['v4'])), 'utf-8'), bytes(str(int(row['v3'])), 'utf-8'),
                bytes(str(int(row['v2'])), 'utf-8'), bytes(str(int(row['v1'])), 'utf-8'))


def read_network_analyzer_file(path):
    data = pd.DataFrame(pd.read_csv(path, skiprows=2))
    data.columns = ['i','freq', 'amp', 'phase']
    # temp = data[data['freq'] > 901e6]
    # data = temp[temp['freq'] < 929e6]
    return np.array(data['freq']), np.array(data['amp']), np.radians(np.array(data['phase']))


# convert s parameter to z parameter
def s2z(s):
    return 50 * (1 + s) / (1 - s)


# convert z parameters to gamma
def z2g(zl, za):
    return (zl - np.conj(za)) / (zl + za)


def get_theta(s, row, phi):
    h = []
    for row_ele, phi_ele in zip(row, phi):
        h.append([1, 1 - row_ele * np.cos(phi_ele), row_ele * np.sin(phi_ele)])

    out = np.matmul(np.matmul(np.linalg.inv(np.matmul(np.transpose(h), h)), np.transpose(h)), s)
    return math.atan2(out[2], out[1])

def get_amplitude(s, row, phi):
    h = []
    for row_ele, phi_ele in zip(row, phi):
        h.append([1, 1 - row_ele * np.cos(phi_ele), row_ele * np.sin(phi_ele)])

    out = np.matmul(np.matmul(np.linalg.inv(np.matmul(np.transpose(h), h)), np.transpose(h)), s)
    return math.atan2(out[2], out[1])