from ribbn_scripts.ref_functions.util_functions import *
from matplotlib import pyplot as plt
import numpy as np
import os
import json
import pandas as pd

save_folder = 'PV_data_Sept2026_705_995_-7'
os.makedirs(save_folder + '/processed', exist_ok=True)

fns = set()
all_files = os.listdir(save_folder)
for f in all_files:
    if f[:17].count('_') != 5:
        continue
    fns.add(f[:17])

ENABLE_PLOTTING = False

pwr_range = range(-35, -13, 1)
target_mVs = [15, 40]

complete_df = pd.DataFrame(columns=["Tag MAC", "Frequency", "Polynomial", "Inverse"] +
                            [f"Required Power @{t}mV (dBm)" for t in target_mVs])

# raw measurements behind the fits: one row per (tag, frequency), sweeps kept as lists
measurement_rows = []

for fn in fns:
    col_data = read_pickle(save_folder+'/'+fn+'_pv_dat.pkl')

    if ENABLE_PLOTTING:
        plt.figure()
    pv_polynomials = {}
    rows = []
    for freq in range(705, 1000, 10):
        x = col_data[freq][0:len(pwr_range)]
        y = np.array(pwr_range)

        measurement_rows.append({
            "Tag MAC": fn,
            "Frequency": freq,
            "Power (dBm)": [int(pwr) for pwr in y],
            "Voltage (mV)": [float(mV) for mV in x],
        })

        p = np.polyfit(np.log(x), y, 2)
        p_inv = np.polyfit(y, np.log(x), 2)
        pv_polynomials[freq] = {'polynomial': p.tolist(), 'inverse': p_inv.tolist()}

        xnew = np.linspace(min(x), max(x), 100)
        req_pwrs = {}
        for target_mV in target_mVs:
            req_pwr = float(np.polyval(p, np.log(target_mV)))
            req_pwrs[f"Required Power @{target_mV}mV (dBm)"] = req_pwr
            print(f"[{fn}] At frequency: {freq} Mhz, {req_pwr} power is required to achieve {target_mV} mV out of ADC.")

        rows.append({
            "Tag MAC": fn,
            "Frequency": freq,
            "Polynomial": p.tolist(),
            "Inverse": p_inv.tolist(),
            **req_pwrs,
        })

        if ENABLE_PLOTTING:
            plt.plot(x, y, '.')
            beautify_graph(True, 'voltage (mV)', 'pwr (dbm)', 'frequency')
            plt.plot(xnew, np.polyval(p, np.log(xnew)), label=str(freq))

    if ENABLE_PLOTTING:
        plt.title(fn)
        plt.xlabel('mV')
        plt.ylabel('dbm')
        plt.legend()

    complete_df = pd.concat([complete_df, pd.DataFrame(rows)], ignore_index=True)

    with open(f"{save_folder}/processed/{fn}_pv_polynomials.json", 'w') as j_f:
        json.dump(pv_polynomials, j_f, indent=4)

complete_df.to_csv(f"{save_folder}/processed/all_pv_polynomials.csv", index=False)

measurement_df = pd.DataFrame(measurement_rows,
                              columns=["Tag MAC", "Frequency", "Power (dBm)", "Voltage (mV)"])
measurement_df.to_csv(f"{save_folder}/processed/all_pv_measurements.csv", index=False)

if ENABLE_PLOTTING:
    plt.show()


# freq=915
# x = col_data[freq]
# y = np.array(pwr_range)
# # p = np.polyfit(np.log(x), y, 2)
# # p_inv = np.polyfit(y, np.log(x), 3)
#
# # plt.subplot(7, 4, ctr)
# plt.plot(x, y, 'o')
#
# xnew = np.linspace(min(x), max(x), 100)
# ynew = np.linspace(min(y), max(y), 100)
# # plt.plot(xnew, np.polyval(p, np.log(xnew)))
# # plt.plot(np.exp(np.polyval(p_inv, ynew)), ynew)
#
#
#
# plt.show()
