"""
<그림 7-4> 채권, IRS, 선물의 Trilemma 개념도를 재현해서 정적 이미지로 저장.
데이터에서 계산되는 그래프가 아니라 참고용 개념도라서, 한 번만 생성해서
assets/trilemma_diagram.png 로 커밋해두고 앱에서는 st.image로 보여준다.

노드를 정원(true circle)으로 그리기 위해 ax.set_aspect("equal") 사용 - 이게 없으면
figure 가로세로 비율에 따라 원이 타원으로 찌그러져 보인다.
"""
import matplotlib
matplotlib.use("Agg")
import math
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

fig, ax = plt.subplots(figsize=(6.6, 6.6), dpi=150)
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.set_aspect("equal")
ax.axis("off")

# 축
ax.annotate("", xy=(9.6, 0.6), xytext=(0.6, 0.6),
            arrowprops=dict(arrowstyle="-", color="black", lw=1.2))
ax.annotate("", xy=(0.6, 9.6), xytext=(0.6, 0.6),
            arrowprops=dict(arrowstyle="-", color="black", lw=1.2))
ax.text(0.3, 9.3, "선물저평가", fontsize=11, ha="left", va="top")
ax.text(9.6, 0.25, "Swap Spread (IRS-T)", fontsize=10, ha="right", va="top")

# 정육각형 배치 (위 2개 / 좌우 2개 / 아래 2개) - 참고 이미지 레이아웃과 동일한 구조
cx, cy, R = 5.35, 5.3, 2.85
angles = {"fut_irs": 120, "fut_ktb": 60, "ktb_irs": 180, "irs_ktb": 0, "ktb_fut": 240, "irs_fut": 300}
labels = {
    "fut_irs": "Fut buy\nIRS pay", "fut_ktb": "Fut buy\nKTB sell",
    "ktb_irs": "KTB buy\nIRS pay", "irs_ktb": "IRS rcv\nKTB sell",
    "ktb_fut": "KTB buy\nFut sell", "irs_fut": "IRS rcv\nFut sell",
}
nodes = {
    key: (cx + R * math.cos(math.radians(a)), cy + R * math.sin(math.radians(a)))
    for key, a in angles.items()
}

NODE_R = 1.0
for key, (x, y) in nodes.items():
    c = Circle((x, y), radius=NODE_R, facecolor="white", edgecolor="black", lw=1.4, zorder=3)
    ax.add_patch(c)
    ax.text(x, y, labels[key], fontsize=8.8, ha="center", va="center", zorder=4)

# Normal Area 박스 (fut_ktb 오른쪽)
box_x, box_y = nodes["fut_ktb"][0] + 2.05, nodes["fut_ktb"][1]
box = FancyBboxPatch((box_x - 0.85, box_y - 0.4), 1.7, 0.8, boxstyle="round,pad=0.05",
                      facecolor="white", edgecolor="black", lw=1.2, zorder=3)
ax.add_patch(box)
ax.text(box_x, box_y, "Normal Area", fontsize=9, ha="center", va="center", zorder=4)

# 순환 화살표: KTB buy/IRS pay -> Fut buy/IRS pay -> Fut buy/KTB sell -> IRS rcv/KTB sell
#             -> IRS rcv/Fut sell -> KTB buy/Fut sell -> (다시 KTB buy/IRS pay)
loop = ["ktb_irs", "fut_irs", "fut_ktb", "irs_ktb", "irs_fut", "ktb_fut", "ktb_irs"]
for a, b in zip(loop[:-1], loop[1:]):
    xa, ya = nodes[a]
    xb, yb = nodes[b]
    arrow = FancyArrowPatch((xa, ya), (xb, yb),
                             connectionstyle="arc3,rad=0.15",
                             arrowstyle="-|>", mutation_scale=15,
                             shrinkA=NODE_R * 17, shrinkB=NODE_R * 17,
                             color="dimgray", lw=1.2, zorder=2)
    ax.add_patch(arrow)

# 우측 상단/하단 대각선 화살표 + 설명
ax.annotate("", xy=(9.7, 9.4), xytext=(box_x + 0.55, box_y + 0.75),
            arrowprops=dict(arrowstyle="-|>", color="black", lw=1.3))
ax.text(9.75, 9.45, "Loss on IRS pay / Futures buy", fontsize=8.3, ha="right", va="bottom")

ax.annotate("", xy=(9.7, 1.3), xytext=(nodes["irs_ktb"][0] + 0.7, nodes["irs_ktb"][1] - 0.9),
            arrowprops=dict(arrowstyle="-|>", color="black", lw=1.3))
ax.text(9.75, 1.5, "Gain on IRS pay / Futures buy", fontsize=8.3, ha="right", va="bottom")

ax.set_title("<figure 7-4> 채권, IRS, 선물의 Trilemma", fontsize=12, pad=12)

fig.tight_layout()
fig.savefig(__file__.replace("gen_trilemma.py", "trilemma_diagram.png"), dpi=150, facecolor="white")
print("saved")
