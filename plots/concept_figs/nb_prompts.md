# Nano-Banana Prompts — ContextGraph 三大核心能力

**共享风格关键词(保持三图视觉一致):**
` `

调用命令模板:
```bash
node ~/.claude/skills/nano-banana/scripts/generate.mjs \
  --prompt "<PROMPT>" \
  --aspect-ratio 16:9 \
  --size 2K \
  --output <OUT>.png
```

---

## 图 1 — 经验点识别 (Experience Extraction)

**文件名:** `fig_experience_extraction.png`

**Prompt:**
```
A modern isometric 3D technical illustration on a deep navy #0B1437 gradient background, cinematic editorial style, Stripe/Linear docs aesthetic.

LEFT — A long translucent ribbon flowing rightward, textured like a scrolling terminal: lines of code, unified-diff hunks (+/- markers in green/red), shell commands, and stack traces. The ribbon is lit from inside with soft cyan light.

CENTER — The ribbon passes through a floating hexagonal prism labeled with subtle circuit etchings, acting as a distiller. Inside the prism, particles condense into glowing faceted gems.

RIGHT — A neat grid of 9–12 small crystalline nodes hovers in orderly rows, each gem a different hue (teal, amber, violet, rose). Each gem carries a simple icon etched on its face: a lightbulb, a warning triangle, a wrench, a checkmark, a bug. Thin light threads connect the gems, hinting at future linking.

Material: polished glass + frosted metal, soft volumetric cyan and amber accent lighting, subtle bloom, shallow depth of field. Floor: faint grid of hairline guides. Ultra-clean vector-isometric style.

No text, no labels, no watermark. 16:9.
```

2. **Context Graph Construction（图构建）**

Prompt:
```
Isometric 3D technical illustration on a deep navy background, editorial infographic style. Show the process of assembling a knowledge graph: on the left, scattered floating glass-like nodes of various soft colors (each representing an "experience"); in the center, luminous curved threads reach out and connect nodes into a dense, layered 3D network — multiple horizontal strata of nodes representing trajectory → experience → pattern → rule, with vertical links bridging the layers. The final structure on the right stabilizes into an organized, neatly-spaced graph with glowing edges. Warm amber highlights on key hub nodes, cyan links, soft ambient fog, clean hairline grid on the floor. Stripe/Linear docs aesthetic, crystalline glass materials, subtle bloom.

No text, no watermark. 16:9.
```

3. **精准召回（Retrieval）**

Prompt:
```
Isometric 3D technical illustration on a deep navy background, editorial style. On the left, a stylized humanoid agent (minimal geometric robot figure or a floating glowing query orb) emits a warm-amber cone of light — a focused query beam — into a large 3D knowledge graph on the right. The graph is a multi-layered network of glass nodes connected by cyan edges; most nodes are dim and desaturated (greyish-teal), but a small cluster of 5–7 nodes lit in warm amber glows brightly along the beam’s path, connected to each other by illuminated edges, clearly selected as the retrieval result. A faint translucent cone visualizes the retrieval beam. Soft bokeh on unrelated background nodes, fine grid floor, shallow depth of field.

Isometric 3D editorial illustration, deep navy + cyan + amber palette, crystalline glass look, soft volumetric lighting, clean composition, Nano Banana 2 style, 4K, no text, no labels, no watermark, 16:9.
```

---

## 运行(统一输出到 `plots/`)

```bash
cd ~/.claude/skills/nano-banana/scripts
node generate.mjs --prompt "$(< prompt_1.txt)" --output ~/Public/codes/ContextGraph/plots/fig_experience_extraction.png --aspect-ratio 16:9 --resolution 2K --thinking
node generate.mjs --prompt "$(< prompt_2.txt)" --output ~/Public/codes/ContextGraph/plots/fig_context_graph.png       --aspect-ratio 16:9 --resolution 2K --thinking
node generate.mjs --prompt "$(< prompt_3.txt)" --output ~/Public/codes/ContextGraph/plots/fig_retrieval.png           --aspect-ratio 16:9 --resolution 2K --thinking
```
