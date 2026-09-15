import pandas as pd
from matplotlib import pyplot as plt
from ribbn_scripts.hardware_api.hardware import *
from ribbn_scripts.ref_functions.spec_functions import *
from ribbn_scripts.ref_functions.util_functions import *
import numpy as np
import json
import os
import json
import pandas as pd

# fn = 'v32-3'
fns = set()
save_folder="VNA_data_Sept2026"

all_files=os.listdir(save_folder)
for f in all_files:
    if f[:17].count('_')!=5:
        continue
    fns.add(f[:17])

## Frequency range for Sept 2026 experiment is 700MHz to 3GHz

ch_list = [1,2,3,4,5,6,7,8]
# ch_list = [b'ch_1\0\n', b'ch_2\0\n', b'ch_3\0\n', b'ch_4\0\n', b'ch_5\0\n', b'ch_6\0\n', b'ch_7\0\n', b'ch_8\0\n']

complete_df = pd.DataFrame(columns=["Tag MAC", "Channel", "Frequencies", "Phases", "Amplituds"])

for fn in fns:
    ch_ig = []
    ch_l = []
    d = {}
    for ch in ch_list:
        if ch in ch_ig:
            continue

        ch_l.append(ch)
        df = pd.DataFrame(
            pd.read_csv(save_folder+"/"+fn+'_channel_' + str(ch) + '_vna_pwr_' + str(15) + '.csv'))

        # print(df["Frequency"].unique())
        df = df[(700e6 < df['Frequency'])]
        df = df[(3000e6 > df['Frequency'])]
        freq = df['Frequency']
        phase = df[' Formatted Data.1']
        amp = df[' Formatted Data']
        p = np.polyfit(freq, phase, 1)
        xnew = np.linspace(min(freq), max(freq), 100)
        # plt.plot(xnew,np.polyval(p,xnew))
        plt.plot(freq, phase, 'o')
        plt.ylim([-190,190])

        ds = {'freq': list(freq), 'phase': list(phase), 'amp': list(amp)}
        d[ch] = ds


    rows = [
        {
            "Tag MAC": fn,
            "Channel": ch,
            "Frequencies": ds['freq'],
            "Phases": ds['phase'],
            "Amplituds": ds['amp'],
        }
        for ch, ds in d.items()
    ]
    complete_df = pd.concat([complete_df, pd.DataFrame(rows)], ignore_index=True)

    with open(f"{save_folder}/processed/{fn}_s11_poly.json",'w') as j_f:
        json.dump(d, j_f, indent=4)

    # plt.legend(ch_l)
    # plt.xlabel('freq')
    # plt.ylabel('amp')
    # plt.show()

complete_df.to_csv(f"{save_folder}/processed/all_s11_poly.csv", index=False)
