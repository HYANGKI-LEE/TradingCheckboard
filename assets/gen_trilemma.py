"""
<그림 7-4> 채권, IRS, 선물의 Trilemma 개념도를 재현해서 정적 이미지로 저장.
데이터에서 계산되는 그래프가 아니라 참고용 개념도라서, 한 번만 생성해서
assets/trilemma_diagram.png 로 커밋해두고 앱에서는 st.image로 보여준다.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

fig, ax = plt.subplots(figsize=(7, 6), dpi=150)
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.axis("off")

# 축 (테두리만 있는 좌표계처럼 보이게)
ax.annotate("", xy=(9.7, 0.6), xytext=(0.5, 0.6),
            arrowprops=dict(arrowstyle="-", color="black", lw=1.2))
ax.annotate("", xy=(0.5, 9.6), xytext=(0.5, 0.6),
            arrowprops=dict(arrowstyle="-", color="black", lw=1.2))
ax.text(0.15, 9.3, "선물저평가", fontsize=11, ha="left", va="top")
ax.text(9.7, 0.3, "Swap Spread (IRS-T)", fontsize=10, ha="right", va="top")

nodes = {
    "fut_irs":  (3.3, 7.2, "Fut buy\nIRS pay"),
    "fut_ktb":  (6.4, 8.1, "Fut buy\nKTB sell"),
    "ktb_irs":  (1.9, 5.0, "KTB buy\nIRS pay"),
    "irs_ktb":  (6.9, 5.4, "IRS rcv\nKTB sell"),
    "irs_fut":  (5.6, 3.3, "IRS rcv\nFut sell"),
    "ktb_fut":  (2.6, 2.1, "KTB buy\nFut sell"),
}

for x, y, label in nodes.values():
    e = Ellipse((x, y), width=2.15, height=1.25, facecolor="white", edgecolor="black", lw=1.3, zorder=3)
    ax.add_patch(e)
    ax.text(x, y, label, fontsize=8.7, ha="center", va="center", zorder=4)

# Normal Area 박스
box = FancyBboxPatch((7.55, 7.35), 1.9, 0.85, boxstyle="round,pad=0.05",
                      facecolor="white", edgecolor="black", lw=1.2, zorder=3)
ax.add_patch(box)
ax.text(8.5, 7.775, "Normal Area", fontsize=9, ha="center", va="center", zorder=4)

# 순환 화살표 (KTB buy/IRS pay -> Fut buy/IRS pay -> Fut buy/KTB sell -> IRS rcv/KTB sell
#              -> IRS rcv/Fut sell -> KTB buy/Fut sell -> KTB buy/IRS pay)
loop = ["ktb_irs", "fut_irs", "fut_ktb", "irs_ktb", "irs_fut", "ktb_fut", "ktb_irs"]
for a, b in zip(loop[:-1], loop[1:]):
    xa, ya, _ = nodes[a]
    xb, yb, _ = nodes[b]
    arrow = FancyArrowPatch((xa, ya), (xb, yb),
                             connectionstyle="arc3,rad=0.18",
                             arrowstyle="-|>", mutation_scale=14,
                             color="dimgray", lw=1.1, zorder=2)
    ax.add_patch(arrow)

# 우측 상단/하단 대각선 화살표 + 설명
ax.annotate("", xy=(9.8, 9.3), xytext=(8.7, 7.9),
            arrowprops=dict(arrowstyle="-|>", color="black", lw=1.3))
ax.text(9.85, 9.35, "Loss on IRS pay / Futures buy", fontsize=8.3, ha="right", va="bottom")

ax.annotate("", xy=(9.8, 1.6), xytext=(8.4, 4.6),
            arrowprops=dict(arrowstyle="-|>", color="black", lw=1.3))
ax.text(9.85, 1.35, "Gain on IRS pay / Futures buy", fontsize=8.3, ha="right", va="top")

ax.set_title("<figure 7-4> 채권, IRS, 선물의 Trilemma", fontsize=12, pad=14)

fig.tight_layout()
fig.savefig(__file__.replace("gen_trilemma.py", "trilemma_diagram.png"), dpi=150, facecolor="white")
print("saved")
