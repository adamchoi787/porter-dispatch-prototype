# PPT Plan Overview

---

## What Deosn't Change

Slides 2, 3, 6, 7, 11, and 12 from the original `slides.tex` are kept as-is. This avoids unnecessary modifications and focuses changes where the grading criteria / logical flow demanded it.

---

## The Core Argument

1. **The problem is real and measurable.** The current manual dispatch has a 75% KPI violation rate. The data backs this up at the service-type level (e.g., 3F X-ray: 88.2% violation).

2. **The KPI is being measured correctly.** The 15-minute KPI refers to *pickup response time* (from order to porter arrival at pickup), not total task completion time. 57% of routes are geographically incapable of meeting a 15-minute *total* completion time — so reframing this is essential before presenting results.

3. **The system works, within staffing constraints.** OR-Tools reduces pickup time by 35.8% (5 porters) and 27.1% (7 porters). To actually hit the KPI, 15 porters are needed — the algorithm cannot compensate for chronic understaffing.

---

## Presentation Flow (15 slides, 12-minute talk + 3-minute video)

The 15 main slides are divided among three speakers.

### Speaker 1 — Background & Problem (S1–S5)
Establishes *why this problem matters* and *what data we're working with*.

| Slide | Purpose |
|-------|---------|
| S1 | Agenda with speaker labels |
| S2 | Problem overview — manual dispatch, 75% violation rate |
| S3 | Root causes — service-type breakdown, dispatch inefficiencies |
| S4 | Data foundation — 81,349 tasks, 89×89 travel time matrix, how data is used |
| S5 | KPI clarification — what "15 minutes" actually means and why it matters |

Speaker 1 ends by defining the correct evaluation baseline, then hands off to Speaker 2.

### Speaker 2 — System Architecture & Methods (S6–S10)
Explains *how the system was built*, one component per slide, ending with a live demo.

| Slide | Purpose |
|-------|---------|
| S6 | Overall 4-component architecture |
| S7 | LLM NLP layer — zero-shot prompting, DeepSeek vs GPT-4 cost (~200× cheaper) |
| S8 | OR-Tools VRPTW model — graph formulation, soft time windows, rolling-horizon re-optimization |
| S9 | RAG Policy Advisor — similarity retrieval, risk scoring, Q&A mode |
| S10 | End-to-end demo — step-by-step flow + real dashboard screenshots |

Speaker 2 presents no quantitative results — those belong entirely to Speaker 3.

### Speaker 3 — Results & Conclusion (S11–S15)
Shows *how well the system performs* and what it means.

| Slide | Purpose |
|-------|---------|
| S11 | NLP accuracy — 84% all-field, 100% location, 88% priority |
| S12 | Optimizer impact — simulation comparison, Greedy vs OR-Tools pickup time |
| S13 | Fleet sizing recommendation — 15 porters needed to approach KPI |
| S14 | Contributions + future work |
| S15 | Thank you |

---

### Backup slides for Q&A

| Slide | Purpose |
|-------|---------|
| S16 | OR-Tools math |
| S17 | LP model choice rationale |
| S18 | KPI table |
| S19 | Full simulation data |
| S20 | Simulation design details |

---

## Key Structural Decisions (vs. the Original `slides.tex`)

The original slide deck had most of the content but needed restructuring. The main changes:

| Decision | Reason |
|----------|--------|
| Move KPI clarification slide to before results (new S5) | Without this, the optimizer improvement numbers are ambiguous |
| Add dedicated data slide (new S4) | Required by grading criterion LO4; was missing from the original |
| Promote OR-Tools formulation from backup to main (new S8) | Required by LO3; only existed as a backup slide |
| Split the "End-to-End Demo" slide into LLM (S7) + RAG (S9) + Demo (S10) | The original mixed system design with demo, making each component invisible |
| Add speaker labels to agenda | Makes the 3-way MECE split visible to the panel from slide 1 |
| Use real dashboard screenshots in S10 | Required by LO6 (prototype); TikZ mockups do not satisfy this |




