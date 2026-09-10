"""Post-process MPP testing CSVs into per-frequency box plots.

Two plots are produced per CSV:

1. Combined link phase. For every (link, frequency, run exp num) triple, the
   two unidirectional phase measurements (Rx=A,Tx=B and Rx=B,Tx=A) are
   combined as:

       combined_phase = ((phase_dir1 + phase_dir2) / 2) mod 90   # deg, i.e. mod pi/2

   The combined phase varies across "Run Exp Num" for a fixed (link,
   frequency), so each link/frequency pair gets a box across runs.

2. Average voltage irrespective of channel. For every row, the six
   Channel_*_median columns are averaged into one per-row voltage, then
   boxed per (Rx->Tx direction, frequency) across run exp numbers.

Usage:
    python post_processing.py <csv_path> [--out-dir DIR]
"""

import argparse
import sys

import matplotlib.pyplot as plt
import pandas as pd

# dataviz categorical palette (references/palette.md), light-mode steps
CATEGORICAL_COLORS = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]

PHASE_COL = "Unidirectional Phase (deg)"
FREQ_COL = "Frequency (MHz)"
RUN_COL = "Run Exp Num"


def load_combined_phases(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, usecols=["Rx", "Tx", FREQ_COL, RUN_COL, PHASE_COL])
    df["series"] = df.apply(lambda r: "-".join(sorted([r["Rx"], r["Tx"]])), axis=1)

    rows = []
    for (series, freq, run), g in df.groupby(["series", FREQ_COL, RUN_COL]):
        if len(g) != 2:
            print(
                f"warning: expected 2 unidirectional measurements for "
                f"link={series} freq={freq} run={run}, got {len(g)}; skipping",
                file=sys.stderr,
            )
            continue
        combined = ((g[PHASE_COL].iloc[0] + g[PHASE_COL].iloc[1]) / 2) % 90.0
        rows.append({"series": series, "frequency": freq, "run": run, "value": combined})

    return pd.DataFrame(rows)


def load_avg_voltage(csv_path: str) -> pd.DataFrame:
    header = pd.read_csv(csv_path, nrows=0).columns
    median_cols = [c for c in header if c.startswith("Channel_") and c.endswith("_median")]

    df = pd.read_csv(csv_path, usecols=["Rx", "Tx", FREQ_COL, RUN_COL, *median_cols])
    df["series"] = df["Rx"] + " -> " + df["Tx"]
    df["value"] = df[median_cols].mean(axis=1)

    return df[["series", FREQ_COL, RUN_COL, "value"]].rename(columns={FREQ_COL: "frequency", RUN_COL: "run"})


def plot_grouped_box(data: pd.DataFrame, ylabel: str, title: str, legend_title: str, output_path: str) -> None:
    series_list = sorted(data["series"].unique())
    frequencies = sorted(data["frequency"].unique())

    fig, ax = plt.subplots(figsize=(max(8, len(frequencies) * 0.6), 6))

    n_series = len(series_list)
    group_width = 0.8
    box_width = group_width / n_series

    for i, series in enumerate(series_list):
        color = CATEGORICAL_COLORS[i % len(CATEGORICAL_COLORS)]
        boxes = []
        positions = []
        medians = []
        for j, freq in enumerate(frequencies):
            vals = data.loc[(data["series"] == series) & (data["frequency"] == freq), "value"].values
            if len(vals) == 0:
                continue
            boxes.append(vals)
            offset = (i - (n_series - 1) / 2) * box_width
            positions.append(j + offset)
            medians.append(float(pd.Series(vals).median()))

        bp = ax.boxplot(
            boxes,
            positions=positions,
            widths=box_width * 0.85,
            patch_artist=True,
            manage_ticks=False,
            boxprops=dict(facecolor=color, alpha=0.55, color=color, linewidth=1.2),
            medianprops=dict(color=color, linewidth=1.6),
            whiskerprops=dict(color=color, linewidth=1.2),
            capprops=dict(color=color, linewidth=1.2),
            flierprops=dict(
                marker="o", markersize=4, markerfacecolor=color, markeredgecolor=color, alpha=0.6
            ),
        )
        if n_series > 1:
            bp["boxes"][0].set_label(series)

        ax.plot(positions, medians, color=color, linewidth=1.4, linestyle="--", zorder=3)

    ax.set_xticks(range(len(frequencies)))
    ax.set_xticklabels(frequencies)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", color="#e1e0d9", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    if n_series > 1:
        ax.legend(title=legend_title, frameon=False)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"saved plot to {output_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="Path to the MPP testing CSV file")
    parser.add_argument("--out-dir", default=None, help="Directory for output images (default: alongside the CSV)")
    args = parser.parse_args()

    base = args.csv_path.rsplit(".", 1)[0]
    if args.out_dir:
        import os

        base = os.path.join(args.out_dir, os.path.basename(base))

    combined = load_combined_phases(args.csv_path)
    if combined.empty:
        print("no combined phases could be computed", file=sys.stderr)
    else:
        plot_grouped_box(
            combined,
            ylabel="Combined phase (deg, mod 90)",
            title="Combined link phase vs. frequency across run exp numbers",
            legend_title="Link",
            output_path=base + "_combined_phase.png",
        )

    avg_voltage = load_avg_voltage(args.csv_path)
    if avg_voltage.empty:
        print("no average voltages could be computed", file=sys.stderr)
    else:
        plot_grouped_box(
            avg_voltage,
            ylabel="Average voltage across channels (mV)",
            title="Average voltage (all channels) vs. frequency across run exp numbers",
            legend_title="Direction",
            output_path=base + "_avg_voltage.png",
        )


if __name__ == "__main__":
    main()
