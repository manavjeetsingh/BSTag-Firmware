import ast
import pandas as pd
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D

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

# Target frequency (Hz) - the closest available frequency point is used.
target_freq = 2400e6

ch_markers = {
    1: 'o',
    2: 's',
    3: '^',
    4: 'v',
    5: 'D',
    6: '*',
    7: 'P',
    8: 'X',
}


def load_data(csv_path):
    df = pd.read_csv(csv_path)
    df['Frequencies'] = df['Frequencies'].apply(ast.literal_eval)
    df['Phases'] = df['Phases'].apply(ast.literal_eval)
    df['Amplituds'] = df['Amplituds'].apply(ast.literal_eval)
    return df


def closest_amp_phase(row, freq):
    freqs = np.asarray(row['Frequencies'])
    idx = int(np.argmin(np.abs(freqs - freq)))
    return row['Amplituds'][idx], row['Phases'][idx], freqs[idx]


def main():
    df = load_data(f"{save_folder}/processed/all_s11_poly.csv")
    df = df[df['Tag MAC'].isin(tag_macs)]

    tag_colors = plt.cm.tab20(np.linspace(0, 1, max(len(tag_macs), 1)))
    tag_color_map = {mac: tag_colors[i] for i, mac in enumerate(tag_macs)}

    fig, ax = plt.subplots()

    records = []
    for _, row in df.iterrows():
        amp, phase, actual_freq = closest_amp_phase(row, target_freq)
        ax.scatter(
            amp, phase,
            color=tag_color_map[row['Tag MAC']],
            marker=ch_markers[row['Channel']],
            s=80,
            edgecolors='k',
            linewidths=0.5,
        )
        records.append({'Tag MAC': row['Tag MAC'], 'Channel': row['Channel'], 'Amp': amp, 'Phase': phase})

    ax.set_xlabel('Amplitude (dB)')
    ax.set_ylabel('Phase (deg)')
    ax.set_title(f'S11 amp/phase near {target_freq/1e6:.1f} MHz')
    ax.grid(True, alpha=0.3)

    tag_handles = [
        Line2D([0], [0], marker='o', color='w', markerfacecolor=tag_color_map[mac],
               markeredgecolor='k', markersize=8, label=mac)
        for mac in tag_macs
    ]
    ch_handles = [
        Line2D([0], [0], marker=marker, color='k', linestyle='None',
               markersize=8, label=f'ch {ch}')
        for ch, marker in ch_markers.items()
    ]

    legend1 = ax.legend(handles=tag_handles, title='Tag MAC', loc='upper left', bbox_to_anchor=(1.02, 1))
    ax.add_artist(legend1)
    ax.legend(handles=ch_handles, title='Channel', loc='lower left', bbox_to_anchor=(1.02, 0))

    fig.tight_layout()

    stats = pd.DataFrame(records).groupby('Channel').agg(
        amp_mean=('Amp', 'mean'),
        amp_std=('Amp', 'std'),
        amp_range=('Amp', lambda s: s.max() - s.min()),
        phase_mean=('Phase', 'mean'),
        phase_std=('Phase', 'std'),
        phase_range=('Phase', lambda s: s.max() - s.min()),
    )
    print(f'Cross-tag spread per channel near {target_freq/1e6:.1f} MHz ({len(tag_macs)} tags):')
    print(stats.round(3).to_string())

    plt.show()


if __name__ == '__main__':
    main()
