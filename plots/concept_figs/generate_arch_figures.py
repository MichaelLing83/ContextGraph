"""
ContextGraph Architecture Diagrams - Clean Version
1. Three-Layer Memory Architecture
2. Core Technical Capabilities (Retrieval Pipeline)
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from pathlib import Path

OUT = Path(__file__).parent

# Colors
BLUE_DARK = '#1A237E'
BLUE_MED = '#1565C0'
BLUE_LIGHT = '#42A5F5'
BLUE_PALE = '#E3F2FD'
RED = '#CE0E2D'
RED_LIGHT = '#FFCDD2'
ORANGE = '#E65100'
ORANGE_LIGHT = '#FFF3E0'
GREEN = '#2E7D32'
GREEN_LIGHT = '#E8F5E9'
PURPLE = '#6A1B9A'
PURPLE_LIGHT = '#F3E5F5'
GRAY = '#757575'
WHITE = '#FFFFFF'


def box(ax, x, y, w, h, text, fc, ec, fs=10, fw='normal', tc='black', lw=1.5):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.06",
                        facecolor=fc, edgecolor=ec, linewidth=lw, zorder=2)
    ax.add_patch(p)
    ax.text(x + w/2, y + h/2, text, ha='center', va='center',
            fontsize=fs, fontweight=fw, color=tc, zorder=3)


def arrow(ax, x1, y1, x2, y2, c='#555', lw=1.8):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color=c, lw=lw), zorder=4)


# ═══════════════════════════════════════
# Figure 1: Three-Layer Memory Architecture
# ═══════════════════════════════════════
def fig_three_layers():
    fig, ax = plt.subplots(figsize=(15, 9))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 9)
    ax.axis('off')
    fig.patch.set_facecolor(WHITE)

    ax.text(7.5, 8.7, 'ContextGraph: Three-Layer Memory Architecture',
            ha='center', fontsize=20, fontweight='bold', color=BLUE_DARK)

    # ── Layer 3: Community (top) ──
    bg = FancyBboxPatch((0.3, 6.0), 14.4, 2.2, boxstyle="round,pad=0.1",
                         facecolor=GREEN_LIGHT, edgecolor=GREEN, lw=2.5, zorder=0)
    ax.add_patch(bg)
    ax.text(0.8, 7.9, 'Layer 3: Community Layer', fontsize=14, fontweight='bold', color=GREEN)
    ax.text(0.8, 7.5, 'Structural clusters via Label Propagation', fontsize=10, color=GRAY)

    box(ax, 2.5, 6.3, 3.5, 1.0,
        'Community\n781 nodes\nsummary + embedding', GREEN, GREEN, 12, 'bold', WHITE)

    details = ['IN_COMMUNITY: 13,466 edges',
               'Grouped by shared ErrorPatterns',
               'Each cluster has LLM-generated summary']
    for i, d in enumerate(details):
        ax.text(7.5, 7.1 - i * 0.35, d, fontsize=10, color=GREEN)

    # ── Layer 2: Semantic (middle) ──
    bg2 = FancyBboxPatch((0.3, 3.0), 14.4, 2.6, boxstyle="round,pad=0.1",
                          facecolor=ORANGE_LIGHT, edgecolor=ORANGE, lw=2.5, zorder=0)
    ax.add_patch(bg2)
    ax.text(0.8, 5.3, 'Layer 2: Semantic Layer', fontsize=14, fontweight='bold', color=ORANGE)
    ax.text(0.8, 4.9, 'LLM-extracted strategies, deduplicated into canonical rules', fontsize=10, color=GRAY)

    box(ax, 0.6, 3.3, 2.8, 1.2,
        'Strategy\n9,600 nodes\n(LLM-extracted)', '#FF9800', ORANGE, 11, 'bold', WHITE)
    box(ax, 4.2, 3.3, 3.0, 1.2,
        'CanonicalRule\n7,835 nodes\n(cosine dedup, t=0.88)', '#EF6C00', ORANGE, 11, 'bold', WHITE)
    box(ax, 8.0, 3.3, 2.8, 1.2,
        'PlaybookEntry\n8,910 nodes\n(agent-readable format)', '#E65100', ORANGE, 11, 'bold', WHITE)
    box(ax, 11.6, 3.3, 2.8, 1.2,
        'ErrorPattern\n586 nodes\n(ImportError, TypeError...)', RED_LIGHT, RED, 11, 'bold', RED)

    # Arrows within Layer 2
    arrow(ax, 3.4, 3.9, 4.2, 3.9, ORANGE)
    ax.text(3.65, 4.15, 'MERGED_INTO', fontsize=8, color=ORANGE, ha='center', style='italic')
    arrow(ax, 7.2, 3.9, 8.0, 3.9, ORANGE)
    arrow(ax, 7.2, 3.5, 11.6, 3.5, RED, 1.5)
    ax.text(9.4, 3.2, 'ADDRESSES_ERROR (18,584)', fontsize=8, color=RED, ha='center', style='italic')

    # ── Layer 1: Episodic (bottom) ──
    bg1 = FancyBboxPatch((0.3, 0.3), 14.4, 2.2, boxstyle="round,pad=0.1",
                          facecolor=BLUE_PALE, edgecolor=BLUE_MED, lw=2.5, zorder=0)
    ax.add_patch(bg1)
    ax.text(0.8, 2.2, 'Layer 1: Episodic Layer', fontsize=14, fontweight='bold', color=BLUE_DARK)
    ax.text(0.8, 1.8, '1,795 SWE-agent trajectory runs (raw experience)', fontsize=10, color=GRAY)

    box(ax, 0.6, 0.5, 2.8, 1.0,
        'Trajectory\n1,795 nodes', BLUE_LIGHT, BLUE_MED, 12, 'bold', WHITE)
    box(ax, 4.2, 0.5, 3.0, 1.0,
        'Fragment\n13,813 nodes', BLUE_LIGHT, BLUE_MED, 12, 'bold', WHITE)
    box(ax, 8.0, 0.5, 2.8, 1.0,
        'ProblemSummary\n1,795 nodes', BLUE_LIGHT, BLUE_MED, 12, 'bold', WHITE)

    # Fragment subtypes
    subtypes = ['error_recovery', 'exploration', 'successful_fix', 'loop', 'failed_attempt']
    for i, st in enumerate(subtypes):
        ax.text(11.5 + (i % 2) * 2.0, 1.2 - (i // 2) * 0.35, st,
                fontsize=8, color=BLUE_MED, ha='center',
                bbox=dict(boxstyle='round,pad=0.15', facecolor=WHITE, edgecolor=BLUE_LIGHT, lw=0.8))

    # Arrows within Layer 1
    arrow(ax, 3.4, 1.0, 4.2, 1.0, BLUE_MED)
    ax.text(3.7, 1.15, 'HAS_FRAGMENT', fontsize=8, color=BLUE_MED, ha='center', style='italic')
    arrow(ax, 8.0, 1.2, 3.4, 1.2, BLUE_MED)
    ax.text(5.7, 1.35, 'SUMMARIZES', fontsize=8, color=BLUE_MED, ha='center', style='italic')

    # ── Cross-layer arrows ──
    # L1 Trajectory -> L2 Strategy (DERIVED_FROM)
    arrow(ax, 2.0, 1.5, 2.0, 3.3, ORANGE, 2.0)
    ax.text(1.3, 2.5, 'DERIVED_FROM\n(9,600)', fontsize=8, color=ORANGE, ha='center', style='italic')

    # L1 Fragment -> L2 ErrorPattern (CAUSED_ERROR)
    arrow(ax, 6.5, 1.5, 13.0, 3.3, RED, 1.8)
    ax.text(10.5, 2.1, 'CAUSED_ERROR\n(7,807)', fontsize=8, color=RED, ha='center', style='italic')

    # L1 Fragment -> L3 Community (IN_COMMUNITY)
    arrow(ax, 5.0, 1.5, 4.25, 6.3, GREEN, 2.0)
    ax.text(3.8, 4.0, 'IN_COMMUNITY\n(13,466)', fontsize=8, color=GREEN, ha='center',
            style='italic', rotation=68)

    # Stats bar
    ax.text(7.5, 0.1,
            '~45,000 Nodes  |  ~74,000 Edges  |  8 Node Types  |  7 Relationship Types  |  3072-dim Embeddings',
            ha='center', fontsize=10, color=GRAY,
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#F5F5F5', edgecolor='#DDD', lw=1))

    fig.savefig(OUT / 'arch_three_layers.png', dpi=200, bbox_inches='tight')
    plt.close(fig)
    print('  arch_three_layers.png')


# ═══════════════════════════════════════
# Figure 2: Core Technical Capabilities
# ═══════════════════════════════════════
def fig_core_capabilities():
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    ax.axis('off')
    fig.patch.set_facecolor(WHITE)

    ax.text(8, 8.7, 'ContextGraph: Core Technical Capabilities',
            ha='center', fontsize=20, fontweight='bold', color=BLUE_DARK)

    # ═════════════════════════
    # Left Panel: Build Pipeline
    # ═════════════════════════
    bg_l = FancyBboxPatch((0.3, 0.3), 4.8, 8.0, boxstyle="round,pad=0.1",
                           facecolor=PURPLE_LIGHT, edgecolor=PURPLE, lw=2, zorder=0)
    ax.add_patch(bg_l)
    ax.text(2.7, 8.0, 'Offline Build Pipeline', ha='center',
            fontsize=14, fontweight='bold', color=PURPLE)

    stages = [
        ('1. Trajectory Ingestion\n3,591 raw trajectories', '#CE93D8', '194.6s'),
        ('2. Fragment Extraction\nerror-boundary segmentation', '#BA68C8', ''),
        ('3. Strategy Extraction\nLLM (Claude Sonnet)', '#AB47BC', '~11.4h'),
        ('4. Cosine Deduplication\nthreshold=0.88', '#9C27B0', ''),
        ('5. Community Detection\nLabel Propagation', '#7B1FA2', ''),
        ('6. Embedding + Indexing\nVector + BM25 indexes', '#6A1B9A', ''),
    ]

    for i, (text, color, time_label) in enumerate(stages):
        sy = 6.8 - i * 1.1
        box(ax, 0.6, sy, 3.6, 0.8, text, color, PURPLE, 10, 'bold', WHITE, 1.5)
        if time_label:
            ax.text(4.4, sy + 0.4, time_label, fontsize=8, color=PURPLE, style='italic')
        if i < len(stages) - 1:
            arrow(ax, 2.4, sy, 2.4, sy - 0.3, PURPLE, 1.5)

    # ═════════════════════════
    # Center: Retrieval Pipeline
    # ═════════════════════════
    bg_c = FancyBboxPatch((5.5, 0.3), 6.5, 8.0, boxstyle="round,pad=0.1",
                           facecolor=BLUE_PALE, edgecolor=BLUE_MED, lw=2, zorder=0)
    ax.add_patch(bg_c)
    ax.text(8.75, 8.0, 'Online Retrieval Pipeline', ha='center',
            fontsize=14, fontweight='bold', color=BLUE_DARK)
    ax.text(8.75, 7.6, 'HippoRAG-style Three-Channel Fusion', ha='center',
            fontsize=10, color=GRAY)

    # Query
    box(ax, 6.5, 7.0, 4.5, 0.5,
        'Query: error message + task description', '#FFF9C4', '#F9A825', 11, 'bold')

    # Query Rewriter
    box(ax, 7.0, 6.2, 3.5, 0.5,
        'LLM Query Rewriter', '#FFF9C4', '#F9A825', 10)
    arrow(ax, 8.75, 7.0, 8.75, 6.7, '#F9A825', 1.5)

    # Three channels
    box(ax, 5.8, 4.6, 1.8, 1.2,
        'Channel 1\n\nCosine\nVector Search\n(3072-dim)', '#64B5F6', BLUE_MED, 9, 'bold', WHITE)
    box(ax, 7.85, 4.6, 1.8, 1.2,
        'Channel 2\n\nBM25\nFulltext\nSearch', '#42A5F5', BLUE_MED, 9, 'bold', WHITE)
    box(ax, 9.9, 4.6, 1.8, 1.2,
        'Channel 3\n\nPPR\nGraph Walk\n(d=0.5)', '#1E88E5', BLUE_MED, 9, 'bold', WHITE)

    # Arrows to channels
    for cx in [6.7, 8.75, 10.8]:
        arrow(ax, 8.75, 6.2, cx, 5.8, BLUE_MED, 1.5)

    # PPR detail
    ax.text(10.8, 4.4, 'Seed: ErrorPattern\nWeight: 1/degree', fontsize=7,
            color=BLUE_DARK, ha='center', style='italic')

    # RRF
    box(ax, 6.5, 3.4, 4.5, 0.6,
        'RRF Merge:  score(d) = sum  1 / (k + rank)', '#1565C0', BLUE_DARK, 11, 'bold', WHITE)
    for cx in [6.7, 8.75, 10.8]:
        arrow(ax, cx, 4.6, 8.75, 4.0, BLUE_MED, 1.5)

    # MMR
    box(ax, 6.5, 2.4, 4.5, 0.6,
        'MMR Rerank:  lambda=0.7,  diversity=0.3', '#0D47A1', BLUE_DARK, 11, 'bold', WHITE)
    arrow(ax, 8.75, 3.4, 8.75, 3.0, BLUE_DARK, 1.5)

    # Output: two targets
    box(ax, 6.0, 0.6, 2.5, 1.2,
        'SWE-agent\nquery_memory tool\n(via Docker)', GREEN_LIGHT, GREEN, 10, 'bold', GREEN)
    box(ax, 9.0, 0.6, 2.5, 1.2,
        'OpenHands\nMemoryHooks\n(session injection)', GREEN_LIGHT, GREEN, 10, 'bold', GREEN)
    arrow(ax, 8.0, 2.4, 7.25, 1.8, GREEN, 1.8)
    arrow(ax, 9.5, 2.4, 10.25, 1.8, GREEN, 1.8)

    ax.text(8.75, 1.95, 'Playbook Output', fontsize=10, fontweight='bold',
            color=GREEN, ha='center')

    # ═════════════════════════
    # Right Panel: Results
    # ═════════════════════════
    bg_r = FancyBboxPatch((12.5, 3.5), 3.2, 4.8, boxstyle="round,pad=0.1",
                           facecolor=RED_LIGHT, edgecolor=RED, lw=2, zorder=0)
    ax.add_patch(bg_r)
    ax.text(14.1, 8.0, 'A/B Experiment', ha='center',
            fontsize=14, fontweight='bold', color=RED)
    ax.text(14.1, 7.6, 'SWE-bench Verified\nn = 1,796 per group', ha='center',
            fontsize=9, color=GRAY)

    results = [
        ('pass@1', '+8.5pp', 'p = 1.1e-07'),
        ('pass@3', '+11.7pp', 'p = 3.1e-15'),
        ('pass@5', '+12.5pp', 'p < 1e-16'),
        ('Tokens', '-15%', '236K > 201K'),
        ('Failures', '-8.8pp', 'p = 3.8e-08'),
    ]

    for i, (metric, gain, detail) in enumerate(results):
        ry = 6.9 - i * 0.7
        ax.text(12.8, ry, metric, fontsize=11, fontweight='bold', color=BLUE_DARK)
        ax.text(14.0, ry, gain, fontsize=15, fontweight='bold', color=RED)
        ax.text(14.0, ry - 0.25, detail, fontsize=8, color=GRAY)

    # Bottom right: key stats
    bg_s = FancyBboxPatch((12.5, 0.3), 3.2, 2.8, boxstyle="round,pad=0.1",
                           facecolor='#F5F5F5', edgecolor='#999', lw=1.5, zorder=0)
    ax.add_patch(bg_s)
    ax.text(14.1, 2.8, 'System Scale', ha='center',
            fontsize=12, fontweight='bold', color=BLUE_DARK)

    stats = [
        '45,000 nodes',
        '74,000 edges',
        '1,795 trajectories',
        '9,600 strategies',
        '7,835 canonical rules',
        '30,000+ lines of code',
        '193 git commits',
    ]
    for i, s in enumerate(stats):
        ax.text(14.1, 2.4 - i * 0.28, s, fontsize=9, color='#333', ha='center')

    fig.savefig(OUT / 'arch_core_capabilities.png', dpi=200, bbox_inches='tight')
    plt.close(fig)
    print('  arch_core_capabilities.png')


if __name__ == '__main__':
    print('Generating architecture diagrams...')
    fig_three_layers()
    fig_core_capabilities()
    print('Done!')
