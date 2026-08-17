import pickle
import numpy as np
from matplotlib import pyplot as plt


# read a pickle file
def read_pickle(file_path):
    with open(file_path, "rb") as fp:
        variable = pickle.load(fp)
    return variable


# write a pickle file
def write_pickle(file_path, variable):
    with open(file_path, "wb") as fp:
        pickle.dump(variable, fp)


def l2a(list_var):
    return np.array(list_var)


def beautify_graph(grid_on, xl, yl, tl):
    if grid_on:
        plt.grid(True, which='major', color='#666666', linestyle='-')
        plt.minorticks_on()
        plt.grid(True, which='minor', color='#999999', linestyle='-.', alpha=0.8)

    plt.xlabel(xl)
    plt.ylabel(yl)
    plt.title(tl)
    plt.legend()


def pa2ri(amp, phase):
    return amp * np.exp(1j * phase)
