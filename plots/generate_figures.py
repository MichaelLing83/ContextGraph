"""
ContextGraph 实验结果可视化
生成转正材料用的图表
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from pathlib import Path

# 全局样式
plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['Arial', 'Helvetica', 'DejaVu Sans'],
    'font.size': 13,
    'axes.titlesize': 16,
    'axes.titleweight': 'bold',
    'axes.labelsize': 14,
    'figure.facecolor': 'white',
    'savefig.dpi': 200,
    'savefig.bbox': 'tight',
})

OUT = Path(__file__).parent
OUT.mkdir(exist_ok=True)

HUAWEI_RED = '#CE0E2D'
HUAWEI_DARK = '#1A1A2E'
CONTROL_COLOR = '#5B8DB8'
TREAT_COLOR = '#CE0E2D'
ACCENT = '#E8A838'


# ─────────────────────────────────────────────
# 图1: A/B 实验 pass@k 对比 (核心结果)
# ─────────────────────────────────────────────
def fig1_passatk():
    fig, ax = plt.subplots(figsize=(8, 5))
    metrics = ['pass@1', 'pass@3', 'pass@5']
    control = [59.2, 67.1, 73.2]
    treatment = [67.8, 78.8, 85.7]
    diffs = ['+8.5pp', '+11.7pp', '+12.5pp']
    pvals = ['p=1.1e-7', 'p=3.1e-15', 'p<1e-16']

    x = np.arange(len(metrics))
    w = 0.32
    bars1 = ax.bar(x - w/2, control, w, label='Control (No Memory)', color=CONTROL_COLOR, edgecolor='white', linewidth=0.5)
    bars2 = ax.bar(x + w/2, treatment, w, label='Treatment (ContextGraph)', color=TREAT_COLOR, edgecolor='white', linewidth=0.5)

    for i, (b1, b2) in enumerate(zip(bars1, bars2)):
        ax.text(b1.get_x() + b1.get_width()/2, b1.get_height() + 1, f'{control[i]}%',
                ha='center', va='bottom', fontsize=12, fontweight='bold', color=CONTROL_COLOR)
        ax.text(b2.get_x() + b2.get_width()/2, b2.get_height() + 1, f'{treatment[i]}%',
                ha='center', va='bottom', fontsize=12, fontweight='bold', color=TREAT_COLOR)
        # 标注提升
        mid_x = x[i]
        ax.annotate(f'{diffs[i]}\n{pvals[i]}',
                    xy=(mid_x, treatment[i]), xytext=(mid_x + 0.42, treatment[i] - 5),
                    fontsize=10, color='#333', fontweight='bold',
                    arrowprops=dict(arrowstyle='->', color='#999', lw=1.2),
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='#FFF3CD', edgecolor='#E8A838', alpha=0.9))

    ax.set_ylabel('Resolution Rate (%)')
    ax.set_title('ContextGraph A/B Experiment on SWE-bench Verified (n=1,796)')
    ax.set_xticks(x)
    ax.set_xticklabels(metrics)
    ax.set_ylim(0, 100)
    ax.legend(loc='upper left', framealpha=0.9)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter())
    fig.savefig(OUT / 'fig1_passatk_ab.png')
    plt.close(fig)
    print('✓ fig1_passatk_ab.png')


# ─────────────────────────────────────────────
# 图2: Token消耗 + 失败率对比
# ─────────────────────────────────────────────
def fig2_efficiency():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Token消耗
    labels = ['Control', 'Treatment']
    tokens = [236111, 200695]
    colors = [CONTROL_COLOR, TREAT_COLOR]
    bars = ax1.bar(labels, [t/1000 for t in tokens], color=colors, width=0.5, edgecolor='white')
    for b, t in zip(bars, tokens):
        ax1.text(b.get_x() + b.get_width()/2, b.get_height() + 3,
                f'{t/1000:.0f}K', ha='center', va='bottom', fontsize=14, fontweight='bold')
    ax1.annotate('-15%', xy=(1, tokens[1]/1000), xytext=(1.35, 230),
                fontsize=16, color=TREAT_COLOR, fontweight='bold',
                arrowprops=dict(arrowstyle='->', color=TREAT_COLOR, lw=2))
    ax1.set_ylabel('Avg Tokens per Problem (K)')
    ax1.set_title('Token Consumption')
    ax1.set_ylim(0, 280)
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    # 失败率
    fail_rates = [68.05, 59.22]
    bars2 = ax2.bar(labels, fail_rates, color=colors, width=0.5, edgecolor='white')
    for b, f in zip(bars2, fail_rates):
        ax2.text(b.get_x() + b.get_width()/2, b.get_height() + 1,
                f'{f:.1f}%', ha='center', va='bottom', fontsize=14, fontweight='bold')
    ax2.annotate('-8.8pp', xy=(1, fail_rates[1]), xytext=(1.35, 66),
                fontsize=16, color=TREAT_COLOR, fontweight='bold',
                arrowprops=dict(arrowstyle='->', color=TREAT_COLOR, lw=2))
    ax2.set_ylabel('Failure Rate (%)')
    ax2.set_title('Failure Rate')
    ax2.set_ylim(0, 85)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)

    fig.suptitle('ContextGraph: Efficiency Gains', fontsize=18, fontweight='bold', y=1.02)
    fig.tight_layout()
    fig.savefig(OUT / 'fig2_efficiency.png')
    plt.close(fig)
    print('✓ fig2_efficiency.png')


# ─────────────────────────────────────────────
# 图3: SWE-bench 模型排行榜 (Top 15)
# ─────────────────────────────────────────────
def fig3_leaderboard():
    models = [
        ('Claude Opus 4.5', 74.4, 0.72),
        ('Gemini 3 Pro', 74.2, 0.46),
        ('GPT-5.2 (high)', 71.8, 0.52),
        ('Sonnet 4.5', 70.6, 0.56),
        ('GPT-5.2', 69.0, 0.27),
        ('Claude 4 Opus', 67.6, 1.13),
        ('GPT-5.1', 66.0, 0.31),
        ('GPT-5.1 Codex', 66.0, 0.59),
        ('GPT-5', 65.0, 0.28),
        ('Claude Sonnet 4', 64.8, 0.37),
        ('Kimi K2 Thinking', 63.4, 0.44),
        ('MiniMax M2', 61.0, 0.43),
        ('DeepSeek V3.2', 60.0, 0.03),
        ('GPT-5 Mini', 59.8, 0.04),
        ('GLM-4.5', 54.2, 0.30),
    ]

    fig, ax = plt.subplots(figsize=(10, 8))
    names = [m[0] for m in reversed(models)]
    rates = [m[1] for m in reversed(models)]
    costs = [m[2] for m in reversed(models)]

    colors = []
    for name in names:
        if 'Claude' in name or 'Sonnet' in name:
            colors.append(TREAT_COLOR)
        elif 'GLM' in name:
            colors.append(ACCENT)
        else:
            colors.append(CONTROL_COLOR)

    bars = ax.barh(names, rates, color=colors, height=0.65, edgecolor='white', linewidth=0.5)
    for b, r, c in zip(bars, rates, costs):
        ax.text(b.get_width() + 0.5, b.get_y() + b.get_height()/2,
                f'{r}%  (${c:.2f}/task)', va='center', fontsize=10)

    ax.set_xlabel('Resolution Rate (%)')
    ax.set_title('SWE-bench Verified Leaderboard (500 problems, bash-only)')
    ax.set_xlim(0, 90)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=TREAT_COLOR, label='Anthropic'),
        Patch(facecolor=CONTROL_COLOR, label='Others'),
        Patch(facecolor=ACCENT, label='GLM (Huawei)')
    ]
    ax.legend(handles=legend_elements, loc='lower right')

    fig.tight_layout()
    fig.savefig(OUT / 'fig3_leaderboard.png')
    plt.close(fig)
    print('✓ fig3_leaderboard.png')


# ─────────────────────────────────────────────
# 图4: 轨迹失败分析 — Loop检测
# ─────────────────────────────────────────────
def fig4_loop_analysis():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

    # 左: Loop数量 vs 成功率
    loop_counts = ['≥1', '≥2', '≥3', '≥5']
    success_rates = [28.7, 6.7, 2.0, 0.3]
    failure_rates = [60.6, 34.2, 16.8, 4.7]
    ratios = [2.1, 5.1, 8.4, 15.7]

    x = np.arange(len(loop_counts))
    w = 0.3
    ax1.bar(x - w/2, success_rates, w, label='Resolved', color='#4CAF50', edgecolor='white')
    ax1.bar(x + w/2, failure_rates, w, label='Unresolved', color='#F44336', edgecolor='white')

    for i, r in enumerate(ratios):
        ax1.text(x[i], max(success_rates[i], failure_rates[i]) + 2,
                f'{r}x', ha='center', fontsize=11, fontweight='bold', color=HUAWEI_DARK)

    ax1.set_xlabel('Number of Loops (≥N)')
    ax1.set_ylabel('Percentage of Trajectories (%)')
    ax1.set_title('Loop Occurrence: Resolved vs Unresolved')
    ax1.set_xticks(x)
    ax1.set_xticklabels(loop_counts)
    ax1.legend()
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    # 右: 失败阶段分布 (饼图)
    stages = ['Early\n(73.6%)', 'Middle\n(20.3%)', 'Late\n(1.0%)', 'No Error\n(5.1%)']
    sizes = [73.6, 20.3, 1.0, 5.1]
    colors_pie = ['#F44336', '#FF9800', '#4CAF50', '#9E9E9E']
    explode = (0.05, 0, 0, 0)
    wedges, texts = ax2.pie(sizes, explode=explode, labels=stages,
                                        colors=colors_pie,
                                        startangle=90, textprops={'fontsize': 12})
    ax2.set_title('Failure Stage Distribution\n(n=3,249 failed trajectories)')

    fig.suptitle('Trajectory Failure Analysis: Loop Detection & Failure Stages', fontsize=16, fontweight='bold', y=1.02)
    fig.tight_layout()
    fig.savefig(OUT / 'fig4_loop_analysis.png')
    plt.close(fig)
    print('✓ fig4_loop_analysis.png')


# ─────────────────────────────────────────────
# 图5: 知识图谱构成 (树状图 / 堆叠柱状图)
# ─────────────────────────────────────────────
def fig5_graph_composition():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

    # 左: 节点类型分布
    node_types = ['Fragment', 'Strategy', 'Playbook\nEntry', 'Canonical\nRule', 'Trajectory', 'Problem\nSummary', 'Community', 'Error\nPattern']
    node_counts = [13813, 9600, 8910, 7835, 1795, 1795, 781, 586]
    colors_nodes = ['#1565C0', '#1976D2', '#1E88E5', '#2196F3', '#42A5F5', '#64B5F6', '#90CAF9', '#BBDEFB']

    bars = ax1.barh(node_types[::-1], node_counts[::-1], color=colors_nodes[::-1], height=0.6, edgecolor='white')
    for b, c in zip(bars, node_counts[::-1]):
        ax1.text(b.get_width() + 200, b.get_y() + b.get_height()/2,
                f'{c:,}', va='center', fontsize=11, fontweight='bold')
    ax1.set_xlabel('Count')
    ax1.set_title(f'Node Types (Total: {sum(node_counts):,})')
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    # 右: 关系类型分布
    rel_types = ['ADDRESSES\n_ERROR', 'HAS\n_FRAGMENT', 'IN\n_COMMUNITY', 'DERIVED\n_FROM', 'MERGED\n_INTO', 'CAUSED\n_ERROR', 'SUMMARIZES']
    rel_counts = [18584, 13813, 13466, 9600, 8910, 7807, 1795]
    colors_rels = ['#C62828', '#D32F2F', '#E53935', '#F44336', '#EF5350', '#E57373', '#EF9A9A']

    bars2 = ax2.barh(rel_types[::-1], rel_counts[::-1], color=colors_rels[::-1], height=0.6, edgecolor='white')
    for b, c in zip(bars2, rel_counts[::-1]):
        ax2.text(b.get_width() + 200, b.get_y() + b.get_height()/2,
                f'{c:,}', va='center', fontsize=11, fontweight='bold')
    ax2.set_xlabel('Count')
    ax2.set_title(f'Relationship Types (Total: {sum(rel_counts):,})')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)

    fig.suptitle('ContextGraph Knowledge Graph Composition\n~45,000 Nodes · ~74,000 Edges · 1,795 Trajectories',
                fontsize=16, fontweight='bold', y=1.05)
    fig.tight_layout()
    fig.savefig(OUT / 'fig5_graph_composition.png')
    plt.close(fig)
    print('✓ fig5_graph_composition.png')


# ─────────────────────────────────────────────
# 图6: 综合仪表盘 (一页总结)
# ─────────────────────────────────────────────
def fig6_dashboard():
    fig = plt.figure(figsize=(14, 8))
    fig.suptitle('ContextGraph: Key Results Summary', fontsize=20, fontweight='bold', y=0.98)

    # 布局: 2x3 grid
    gs = fig.add_gridspec(2, 3, hspace=0.4, wspace=0.35)

    # (0,0) pass@k
    ax1 = fig.add_subplot(gs[0, 0])
    metrics = ['pass@1', 'pass@3', 'pass@5']
    ctrl = [59.2, 67.1, 73.2]
    treat = [67.8, 78.8, 85.7]
    x = np.arange(3)
    ax1.bar(x - 0.15, ctrl, 0.28, label='Control', color=CONTROL_COLOR)
    ax1.bar(x + 0.15, treat, 0.28, label='Treatment', color=TREAT_COLOR)
    ax1.set_xticks(x)
    ax1.set_xticklabels(metrics, fontsize=10)
    ax1.set_ylim(0, 100)
    ax1.set_title('pass@k (%)', fontsize=13)
    ax1.legend(fontsize=8, loc='upper left')
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    # (0,1) 关键数字卡片
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.axis('off')
    kv = [
        ('pass@1 Gain', '+8.5pp', TREAT_COLOR),
        ('Token Saved', '15%', TREAT_COLOR),
        ('Graph Scale', '45K nodes', HUAWEI_DARK),
        ('Trajectories', '1,795', HUAWEI_DARK),
        ('Strategies', '8,895', HUAWEI_DARK),
        ('Code Lines', '30K+', HUAWEI_DARK),
    ]
    for i, (label, val, color) in enumerate(kv):
        row, col = divmod(i, 2)
        ax2.text(0.05 + col * 0.5, 0.85 - row * 0.35, val,
                fontsize=20, fontweight='bold', color=color, transform=ax2.transAxes)
        ax2.text(0.05 + col * 0.5, 0.72 - row * 0.35, label,
                fontsize=10, color='#666', transform=ax2.transAxes)
    ax2.set_title('Key Metrics', fontsize=13)

    # (0,2) 策略提取管线
    ax3 = fig.add_subplot(gs[0, 2])
    pipeline = ['Trajectories\n1,795', 'Fragments\n16,587', 'Strategies\n8,895', 'Canonical\nRules\n7,835']
    y_pos = [3, 2, 1, 0]
    ax3.barh(y_pos, [1795, 16587, 8895, 7835], color=['#1565C0', '#1976D2', '#2196F3', '#42A5F5'],
             height=0.6, edgecolor='white')
    ax3.set_yticks(y_pos)
    ax3.set_yticklabels(pipeline, fontsize=10)
    ax3.set_title('Knowledge Extraction Pipeline', fontsize=13)
    ax3.spines['top'].set_visible(False)
    ax3.spines['right'].set_visible(False)

    # (1,0) 失败模式
    ax4 = fig.add_subplot(gs[1, 0])
    types = ['loop_trap', 'error_accum', 'tech_error', 'unknown']
    counts = [34, 34, 30, 2]
    colors_f = ['#F44336', '#FF9800', '#FFC107', '#9E9E9E']
    ax4.pie(counts, labels=types, colors=colors_f, autopct='%1.0f%%', startangle=90, textprops={'fontsize': 9})
    ax4.set_title('Failure Types', fontsize=13)

    # (1,1) Loop vs Success
    ax5 = fig.add_subplot(gs[1, 1])
    cats = ['≥1', '≥2', '≥3', '≥5']
    succ = [28.7, 6.7, 2.0, 0.3]
    fail = [60.6, 34.2, 16.8, 4.7]
    x5 = np.arange(4)
    ax5.bar(x5 - 0.15, succ, 0.28, label='Resolved', color='#4CAF50')
    ax5.bar(x5 + 0.15, fail, 0.28, label='Unresolved', color='#F44336')
    ax5.set_xticks(x5)
    ax5.set_xticklabels(cats, fontsize=10)
    ax5.set_ylabel('%')
    ax5.set_title('Loops vs Outcome', fontsize=13)
    ax5.legend(fontsize=8)
    ax5.spines['top'].set_visible(False)
    ax5.spines['right'].set_visible(False)

    # (1,2) Intervention potential
    ax6 = fig.add_subplot(gs[1, 2])
    levels = ['High\n(32.1%)', 'Medium\n(45.3%)', 'Low\n(22.6%)']
    vals = [32.1, 45.3, 22.6]
    colors_int = ['#4CAF50', '#FFC107', '#F44336']
    ax6.bar(levels, vals, color=colors_int, width=0.5, edgecolor='white')
    for i, v in enumerate(vals):
        ax6.text(i, v + 1, f'{v}%', ha='center', fontweight='bold', fontsize=11)
    ax6.set_ylabel('%')
    ax6.set_title('Intervention Potential\n(95% failures matched)', fontsize=13)
    ax6.set_ylim(0, 60)
    ax6.spines['top'].set_visible(False)
    ax6.spines['right'].set_visible(False)

    fig.savefig(OUT / 'fig6_dashboard.png')
    plt.close(fig)
    print('✓ fig6_dashboard.png')


if __name__ == '__main__':
    print(f'Saving figures to {OUT}/')
    fig1_passatk()
    fig2_efficiency()
    fig3_leaderboard()
    fig4_loop_analysis()
    fig5_graph_composition()
    fig6_dashboard()
    print('\nDone! All figures saved.')
