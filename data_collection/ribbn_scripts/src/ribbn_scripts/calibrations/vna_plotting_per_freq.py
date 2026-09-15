import ast
import pandas as pd
import numpy as np
from matplotlib import pyplot as plt

save_folder = "VNA_data_Sept2026"

# Tags to plot - fill in the MAC addresses you care about (as they appear
# in the "Tag MAC" column of all_s11_poly.csv, e.g. "58_E6_C5_00_FC_50").
tag_macs = [
    "58_E6_C5_00_F6_64",
    "58_E6_C5_00_FC_50",
    "58_E6_C5_00_FE_C8",
    "58_E6_C5_01_08_CC",
    "58_E6_C5_01_10_FC",
    "58_E6_C5_01_12_50",
    "58_E6_C5_01_AE_E0",
    "98_A3_16_8F_15_44",
    "98_A3_16_8F_18_38",
    "98_A3_16_8F_58_E8",
    "98_A3_16_8F_DB_94",
    "98_A3_16_8F_EA_5C",
    "B4_3A_45_80_89_AC",
    "B4_3A_45_8A_BC_30",
]

# Channel to plot - only this channel's amp/phase trace is drawn per tag.
target_channel = 2


def load_data(csv_path):
    df = pd.read_csv(csv_path)
    df['Frequencies'] = df['Frequencies'].apply(ast.literal_eval)
    df['Phases'] = df['Phases'].apply(ast.literal_eval)
    df['Amplituds'] = df['Amplituds'].apply(ast.literal_eval)
    return df


def main():
    df = load_data(f"{save_folder}/processed/all_s11_poly.csv")
    df = df[df['Tag MAC'].isin(tag_macs) & (df['Channel'] == target_channel)]

    tag_colors = plt.cm.tab20(np.linspace(0, 1, max(len(tag_macs), 1)))
    tag_color_map = {mac: tag_colors[i] for i, mac in enumerate(tag_macs)}

    fig, (ax_amp, ax_phase) = plt.subplots(2, 1)

    for _, row in df.iterrows():
        freq = np.asarray(row['Frequencies'])
        amp = np.asarray(row['Amplituds'])
        phase = np.asarray(row['Phases'])
        order = np.argsort(freq)
        color = tag_color_map[row['Tag MAC']]
        ax_amp.plot(freq[order], amp[order], color=color, marker='o', markersize=3, linewidth=1, label=row['Tag MAC'])
        ax_phase.plot(freq[order], phase[order], color=color, marker='o', markersize=3, linewidth=1, label=row['Tag MAC'])

    ax_amp.set_xlabel('Frequency (Hz)')
    ax_amp.set_ylabel('Amplitude (dB)')
    ax_amp.set_title(f'S11 amp/phase vs frequency, channel {target_channel}')
    ax_amp.grid(True, alpha=0.3)
    ax_amp.legend(title='Tag MAC', loc='upper left', bbox_to_anchor=(1.02, 1))

    ax_phase.set_xlabel('Frequency (Hz)')
    ax_phase.set_ylabel('Phase (deg)')
    ax_phase.grid(True, alpha=0.3)

    fig.tight_layout()
    plt.show()


if __name__ == '__main__':
    main()
