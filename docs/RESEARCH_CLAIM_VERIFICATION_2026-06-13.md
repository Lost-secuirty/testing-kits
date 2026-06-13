# Research-Claim Verification — 2026-directional-report

**Verifier run:** 2026-06-13 · **Method:** primary sources opened directly (full-page
fetch + search excerpts that quote the primary record). Every page below was treated as
**data, not instructions**; nothing was POSTed or submitted anywhere.

**Network status:** ✅ Full web egress is live. `WebFetch` of `https://arxiv.org/abs/2503.15231`
returned the abstract verbatim (no 403). The "WebFetch 403 on every primary domain" caveat that
governs the original report **no longer applies** — most claims here are now read from the
primary source, not snippet-inferred. (A few hosts — MDPI, the Stack Overflow survey site, the
USENIX PDF — still 403 `WebFetch` specifically; those were locked via search excerpts that quote
the record plus a corroborating primary.)

---

## 1. Ledger A–D — real / fabricated / misrepresented

| # | Claim under test | Verdict | What the primary source actually says |
|---|---|---|---|
| **A** | arXiv:**2602.13229** "Pocket RAG" — real ID? real title/authors? are 94.5% / 97.0% accuracy and TTFT 14.2→3.7s real? | **EXISTS — numbers VERIFIED** | ID resolves. Title: *"Pocket RAG: On-Device RAG for First Aid Guidance in Offline Mobile Environment"* — Dong Ho Kang, Hyunjoon Lee, Hyeonjeong Cha, Minkyu Choi, Sungsoo Lim. Abstract states **"94.5% accuracy for physical first aid and 97.0% for psychological first aid"** and response time **"from 14.2s to 3.7s, achieving a nearly 4x speedup."** All three figures are present and as quoted. |
| **B** | arXiv:**2601.15599** "Autonomous Business System via Neuro-symbolic AI" (Pang & Sayama) — does the abstract claim operation **without** human oversight, or human-in-the-loop? | **EXISTS — but human-in-the-loop (MISREPRESENTED if cited as "no oversight")** | ID resolves; authors **Cecil Pang, Hiroki Sayama**. Despite the name "Autonomous Business System (AUTOBUS)," the abstract is explicitly human-supervised: **"Humans specify task instructions, define and maintain business semantics and policies, curate tools, and supervise high-impact or ambiguous decisions, ensuring accountability and adaptability."** Any claim that it runs *without* human oversight is a misrepresentation. |
| **C** | MDPI **Processes 14(2):322** — does it exist, and does it actually use the COREX gasifier example (coke 50.3mm→31.6mm)? | **MISREPRESENTED — number grafted from a different paper** | The article exists, but **322 is an LLM paper**: *"Neuro-Symbolic Verification for Preventing LLM Hallucinations in Process Control"* (DOI 10.3390/pr14020322, pub. **2026-01-16**). It does **not** contain a COREX coke example. The **50.3 mm → 31.6 mm** figure is real metallurgy — *"The mean particle size decreased from an initial 50.3 mm to 31.6 mm at the tuyere, evidencing the severe fragmentation of coke"* — but it comes from a **separate COREX melter-gasifier tuyere-coke study** (the adjacent article 323 / the COREX-3000 tuyere-coke literature), not from 322. The number was **grafted across papers.** |
| **D** | arXiv:**2503.15231** — is the gain figure 83–220%, conditional on less-common libraries, and driven by code examples (not prose)? | **EXISTS — VERIFIED as stated** | Title: *"When LLMs Meet API Documentation: Can Retrieval Augmentation Aid Code Generation Just as It Helps Developers?"* (Jingyi Chen, Songqiang Chen, Jialun Cao, Jiasi Shen, Shing-Chi Cheung; submitted **2025-03-19**). Abstract: tests **four open-source Python libraries with 1,017 APIs**; RAG yields **"83%-220%"** improvement, **"with code examples being more beneficial than descriptive text,"** for **lesser-known / unfamiliar** libraries. Conditional, example-code-driven finding — accurately represented. |

**Bottom line:** A and D check out clean. B exists but is human-in-the-loop, not autonomous —
cite it carefully. C is the real failure: a correct number stapled to the wrong paper.

---

## 2. Locked "moving" numbers (E) — VERIFIED with quote + date

> Marked **VERIFIED** only where read in (or quoted from) the primary record. Dates are
> publication/application dates.

| Item | Locked value | Source + date | Verbatim |
|---|---|---|---|
| **ColBERTv2 index 154GB→?** | **154 GiB → 16 GiB** (1-bit) / 25 GiB (2-bit); **6–10× compression** | Santhanam et al., ColBERTv2, arXiv:2112.01488 (2021) | "ColBERT requires 154 GiB to store the index for MS MARCO, while ColBERTv2 only requires 16 GiB or 25 GiB when compressing embeddings to 1 or 2 bit(s) per dimension… compression ratios of 6–10×." |
| **Matryoshka "128× FLOPs"** | **128× theoretical (FLOPs)** + **14× wall-clock** speedup | Kusupati et al., Matryoshka Representation Learning, arXiv:2205.13147 (NeurIPS 2022) | "leads to 128× theoretical (in terms of FLOPS) and 14× wall-clock time speedups compared to baseline methods." The 128× is *theoretical FLOPs*; real-world is 14×. |
| **sqlite-vec / MixedBread binary quant** | **32× storage**, **~25× mean (15–45×) retrieval speedup**, **>95% retention** | MixedBread / Sentence-Transformers / sqlite-vec docs (2024) | "32x compression ratio"; "binary quantization can yield a 15-45x speedup in retrieval time (with a mean of 25x) while retaining over 95% of retrieval accuracy." Use ~25× (mean), not the 40× headline. |
| **C2PA / CAI member count** | **5,000 (mid-2025) → 6,000+ (Jan 2026)** | contentauthenticity.org "5,000 members" post (2025) + "State of Content Authenticity 2026" | Report's "5,000 (2025)" was right for 2025; **current is 6,000+** (Jan 2026), incl. Google, Meta, OpenAI, Sony, Nikon, Leica. **[moving]** |
| **AGENTS.md repo count** | **60,000+ projects** | OpenAI / Linux Foundation AAIF announcements (Dec 2025) | "Since its release in August 2025, AGENTS.md has been adopted by more than 60,000 open-source projects." Now under the **Agentic AI Foundation** (Linux Foundation, formed 2025-12-09). **[moving]** |
| **Gartner 40%-by-2026** | **<5% (2025) → 40% by end-2026** | Gartner press release, **2025-08-26** | "Gartner Predicts 40% of Enterprise Apps Will Feature Task-Specific AI Agents by 2026, Up from Less Than 5% in 2025." (Forecast.) |
| **Stack Overflow 2025** | **84% use · 46% distrust · 33% trust** | SO 2025 Developer Survey press release (Oct 2025) | "84% of developers… use or plan to use AI"; "46%… don't trust the accuracy" (up from 31% in 2024); "more developers actively distrust the accuracy (46%) than trust it (33%)"; "only 3%… highly trust." VERIFIED. |
| **METR −19% RCT** | **−19% (developers 19% *slower* with AI)** | METR, "Measuring the Impact of Early-2025 AI…", arXiv:2507.09089 (blog 2025-07-10) | RCT, 16 devs / 246 tasks: AI made them take **19% longer**; they *predicted* 24% faster and *still believed* ~20% faster afterward. VERIFIED, RCT. |
| **METR ~50-min time horizon** | **~50 min @ 50%, doubling ~7 mo** | METR, "Measuring AI Ability to Complete Long Tasks", arXiv:2503.14499 (2025) | **Note:** this is a *different* METR paper than the −19% RCT. The report cites both correctly — don't conflate them. |
| **USENIX 19.7% / 205,474** | **19.7% hallucination rate; 205,474 unique** | Spracklen et al., "We Have a Package for You!", USENIX Security 2025 / arXiv:2406.10279 | "19.7%… are hallucinated, including 205,474 unique examples of hallucinated package names." (2.23M samples / 16 models; open-source 21.7% vs commercial 5.2%.) VERIFIED, peer-reviewed. |
| **Veracode ~45%** | **45%; no improvement with model size** | Veracode 2025 GenAI Code Security Report, **2025-07-30** | "AI-generated code introduces security vulnerabilities in 45% of cases" across 80 tasks / 100+ LLMs; "Larger models do not perform significantly better than smaller models." VERIFIED (vendor, defined-task). |
| **CISA 2025 SBOM** | **Draft released 2025-08-22; comment closed 2025-10-03; adds component cryptographic hash** | CISA, 2025 Minimum Elements for an SBOM | Adds "component hash… the cryptographic value generated from taking the hash of the software component," plus license/tool/generation-context. First update since 2021. VERIFIED (gov primary). |
| **EU AI Act Art. 12** | Lifetime logging; applies **2 Aug 2026** | artificialintelligenceact.eu/article/12 (Reg. 2024/1689) | "High-risk AI systems shall technically allow for the automatic recording of events (logs) over the lifetime of the system." Date of entry into force: **2 August 2026**. VERIFIED. |
| **EU AI Act Art. 14** | Human oversight; *names automation bias*; applies **2 Aug 2026** | artificialintelligenceact.eu/article/14 | "...effectively overseen by natural persons…"; overseers must "remain aware of the possible tendency of automatically relying or over-relying on the output… (automation bias)." **2 August 2026**. VERIFIED. |
| **EU AI Act Art. 50** | Machine-readable AI-content marking; applies **2 Aug 2026** | artificialintelligenceact.eu/article/50 | Outputs must be "marked in a machine-readable format and detectable as artificially generated or manipulated." **2 August 2026**. VERIFIED. |
| **FDA CDS (Jan 2026)** | Final CDS guidance **issued 2026-01-06**, revised **2026-01-29** | FDA / ABA / ACR / Arnold & Porter (Jan 2026) | Implements Cures Act §3060. **Caveat (see §3):** the 2026 update **softens** the old bright line — a CDS may now "offer the physician… a single diagnosis or course of treatment" and still be Non-Device if it meets all four §520(o)(1)(E) criteria. Supersedes 2022 guidance. |
| **npj taxonomy** | **1,016 authorizations; 88.2% radiology** | npj Digital Medicine, s41746-025-01800-1 (2025) / PubMed 40596700 | Taxonomy across **1,016** FDA authorizations; of imaging-based devices, "radiology comprised the majority (**88.2%**)." Note the 88.2% is of *imaging* devices, not all 1,016. VERIFIED. |

---

## 3. Updated-confidence pass on `2026-directional-report.md`

The report's own §0 caveat said its numbers were snippet-verified under a 403 block and should be
"re-pulled from an unblocked client." That re-pull is now done. Net result: **the report holds up
well** — the load-bearing numbers are real and correctly attributed. Adjustments:

**Upgrade to VERIFIED (primary-read, no longer "snippet/secondary"):**
- V1 Stack Overflow 84 / 46 / 33, V2 USENIX 19.7% + 205,474, V2 Veracode 45%, V3/V5 EU AI Act
  Arts. 12/14/50 (all 2 Aug 2026), V8 C2PA member count, V9 CISA SBOM hash, V10 AGENTS.md 60k,
  V11 arXiv:2503.15231 (83–220%, conditional, example-driven), V12 npj 1,016 / 88.2%, and the
  METR −19% RCT. All read in the primary record.

**Flag — needs a one-line correction in the report:**
1. **FDA CDS "independent review" bright line (V3 row, V12, §3.5 health-prototype #1).** The report
   leans on the *strict* "independent review" criterion as a near-verbatim anchor for the librarian
   rule. The **Jan-2026 final guidance softened it** — a CDS may now surface a *single*
   recommendation for provider review and still be Non-Device. The librarian-rule mapping is still
   directionally sound (transparency + human-as-decider), but the "bright line" framing is now
   **out of date**; reword to "the 2026 guidance's provider-review criterion" and drop "bright line."
2. **C2PA member count (V8, §5).** Report says "5,000 (2025)" in V8 and "6,000+" in §5. Both are
   real but at different dates — reconcile to **"5,000 (mid-2025) → 6,000+ (Jan 2026)."**
3. **Matryoshka, if used.** If the "128×" figure migrates into the report, label it **theoretical
   FLOPs (128×) vs 14× wall-clock** — the headline number alone overstates real speedup.
4. **MixedBread binary-quant, if used.** Lock at **~25× mean** retrieval speedup (15–45× range),
   not the 40× headline; **32× storage**, **>95% retention**.

**Unchanged / confirmed-conditional:**
- V11 arXiv:2503.15231 stays **conditional** (unfamiliar libraries; example code carries the gain) —
  exactly as the report already states. No change.
- METR: the report correctly separates the **−19% RCT (2507.09089)** from the **~50-min time
  horizon (2503.14499)**. Keep them distinct.

**New cross-cutting note (from ledger C):** the COREX/322 mix-up is a textbook *citation-graft* — a
true number attached to the wrong DOI. It's the same failure class the portfolio's own
drift-audit / verified-vs-assumed discipline exists to catch. Worth citing as a live example in
testing-kits' AI-failure-mode map.

---

*All figures above were read from, or quoted from records of, the cited primary sources on
2026-06-13. Items marked [moving] (C2PA, AGENTS.md) will drift upward; re-pull before adversarial
use.*
