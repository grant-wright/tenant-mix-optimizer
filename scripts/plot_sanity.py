#!/usr/bin/env python
"""
Visual sanity check for the synthetic data (Day 3).
Reads data/{tenants,observations}.json and saves data/sanity_plots.png.

Usage:
    python scripts/plot_sanity.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import pandas as pd

DATA = Path("data")
DEMOS = ["Atelier Margot", "Pancho's Tacos", "Crystal Mobile", "Pages & Co"]


def main():
    tenants = pd.DataFrame(json.loads((DATA / "tenants.json").read_text()))
    obs = pd.DataFrame(json.loads((DATA / "observations.json").read_text()))
    obs = obs.merge(tenants[["tenant_id", "status", "name"]], on="tenant_id", how="left")
    obs = obs.sort_values(["tenant_id", "month"])

    fig, ax = plt.subplots(2, 2, figsize=(13, 9))

    # (1) Demo tenants — rent-to-sales over time
    for name in DEMOS:
        d = obs[obs.name == name]
        ax[0, 0].plot(range(len(d)), d["rent_to_sales_ratio"].values, label=name)
    ax[0, 0].axhline(0.15, ls="--", c="grey", lw=0.8, label="apparel healthy ceiling")
    ax[0, 0].set_title("Demo tenants — rent-to-sales over time")
    ax[0, 0].set_xlabel("month index"); ax[0, 0].set_ylabel("rent_to_sales_ratio")
    ax[0, 0].legend(fontsize=8)

    # (2) Demo tenants — stock depth over time (leading signal)
    for name in DEMOS:
        d = obs[obs.name == name]
        ax[0, 1].plot(range(len(d)), d["stock_depth_index"].values, label=name)
    ax[0, 1].set_title("Demo tenants — stock depth over time")
    ax[0, 1].set_xlabel("month index"); ax[0, 1].set_ylabel("stock_depth_index")
    ax[0, 1].legend(fontsize=8)

    # (3) Exit timing — months observed before exit
    exited_ids = set(tenants.loc[tenants.status == "exited", "tenant_id"])
    months_obs = obs[obs.tenant_id.isin(exited_ids)].groupby("tenant_id").size()
    ax[1, 0].hist(months_obs.values, bins=18, color="tab:purple", alpha=0.8)
    ax[1, 0].set_title(f"Exit timing — months observed before exit (n={len(exited_ids)})")
    ax[1, 0].set_xlabel("months until exit"); ax[1, 0].set_ylabel("tenants")

    # (4) rent-to-sales at final observed month — exited vs active
    last = obs.groupby("tenant_id", as_index=False).tail(1)
    for st, c in [("exited", "tab:red"), ("active", "tab:green")]:
        ax[1, 1].hist(last.loc[last.status == st, "rent_to_sales_ratio"],
                      bins=20, alpha=0.5, label=st, color=c)
    ax[1, 1].set_title("rent-to-sales (final obs) — exited vs active")
    ax[1, 1].set_xlabel("rent_to_sales_ratio"); ax[1, 1].set_ylabel("tenants")
    ax[1, 1].legend(fontsize=8)

    fig.suptitle("Synthetic data sanity check (v5 branched generator)", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = DATA / "sanity_plots.png"
    fig.savefig(out, dpi=110)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
