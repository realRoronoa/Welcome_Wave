import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as md
from matplotlib import font_manager as fm
from datetime import date
fm.fontManager.addfont("/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf")
fm.fontManager.addfont("/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf")
plt.rcParams["font.family"] = "Carlito"
rows = [("Sprint 0  setup, CI/CD, schema", date(2026,10,14), date(2026,10,16), "#9aa6c2"),
        ("Sprint 1  index, chat, Supervisor", date(2026,10,17), date(2026,10,23), "#9aa6c2"),
        ("Sprint 2  meetings, KT links, tasks, review", date(2026,10,24), date(2026,10,30), "#0b7a75"),
        ("Sprint 3  hardening, load test, bug bash", date(2026,10,31), date(2026,11,4), "#9aa6c2"),
        ("Buffer  rehearsal, final docs", date(2026,11,5), date(2026,11,6), "#d6dce6")]
fig, ax = plt.subplots(figsize=(10, 3.0), dpi=200)
for i, (name, a, b, c) in enumerate(rows):
    ax.barh(i, (b - a).days + 1, left=md.date2num(a), height=0.5, color=c, edgecolor="none")
    ax.text(md.date2num(b) + 1.2, i, f"{a:%b} {a.day} to {b:%b} {b.day}", va="center", fontsize=9.5, color="#55607a")
ax.set_yticks(range(len(rows))); ax.set_yticklabels([r[0] for r in rows], fontsize=10.5, color="#1d2742")
ax.invert_yaxis()
rel = md.date2num(date(2026,11,4)) + 1
ax.axvline(rel, color="#c8403a", lw=1.2, ls="--")
ax.text(rel, -0.75, "release branch clean, Nov 4", color="#c8403a", fontsize=9.5, ha="center", fontweight="bold")
today = md.date2num(date(2026,10,15)) + 0.5
ax.axvline(today, color="#1d2742", lw=0.8, ls=":")
ax.text(today + 0.3, -0.75, "today, Oct 15", fontsize=9, ha="left", color="#1d2742")
ax.xaxis.set_major_locator(md.DayLocator(bymonthday=[14, 21, 28, 4]))
ax.xaxis.set_major_formatter(md.DateFormatter("%b %d"))
ax.set_xlim(md.date2num(date(2026,10,13)), md.date2num(date(2026,11,12)))
for s in ["top", "right", "left"]: ax.spines[s].set_visible(False)
ax.tick_params(axis="y", length=0); ax.tick_params(axis="x", colors="#55607a", labelsize=9.5)
ax.grid(axis="x", color="#edf0f5"); ax.set_axisbelow(True)
plt.tight_layout(); plt.savefig("sprints.png", bbox_inches="tight")
