# Automatic Basic To Cloze

[![AnkiWeb page](https://img.shields.io/badge/AnkiWeb-addon-blue.svg)](https://ankiweb.net/shared/info/800723229)

Patreon link of original author:
[![Donate via patreon](https://img.shields.io/badge/patreon-donate-green.svg)](https://www.patreon.com/trgk)


Automatically convert cloze-y things to cloze type.

![Example image](screenshots/basic2cloze.gif)


# Multiple Disjoint DP Groups, Each with Multiple Disjoint TP Groups (Heterogeneous VRAM)

## 1. Problem Overview

We have **N** GPUs, labeled \(g = 1,2,\dots,N\). Each GPU \(g\) has:

- \(\text{VRAM}_g\) (memory capacity)  
- \(\text{BW}_{g,h}\) (bandwidth to others; used if modeling communication costs)

The total parameter size of the model is \(\text{ParamSize}\). We aim to distribute it using:

1. **Data Parallel (DP)**: Multiple disjoint DP groups \(\bigl(\text{DPGroup}_1,\ldots,\text{DPGroup}_D\bigr)\).  
2. **Tensor Parallel (TP)**: Within each DP group \(i\), multiple disjoint TP groups \(\bigl(\text{TPGroup}_{i,1},\ldots,\text{TPGroup}_{i,K_i}\bigr)\).

A single GPU belongs to exactly one DP group, and within that DP group, at most one TP group. GPUs can have heterogeneous VRAM. Each group arrangement must store the model’s parameters plus associated memory (gradients, optimizer states, partial buffers if needed). The objective is to ensure memory feasibility and minimize overall communication costs.

---

## 2. Explanation of \(\kappa\) (Parameters + Grads + Optimizer States + Misc Buffers)

We use a multiplier \(\kappa\) to capture more than just raw parameter memory. For example:

- **Gradients**: often same size as parameters.  
- **Optimizer States**: in Adam, typically two buffers (m, v) ~ same size as parameters.  
- **Misc. Buffers**: partial all-gather or reduce-scatter buffers for ZeRO-3 (FSDP) or similar.

Hence, with Adam, one might approximate \(\kappa \approx 4\) (1 for parameters + 1 for gradients + 2 for m and v). For simpler SGD + momentum, \(\kappa\) could be around 2 or 3. This single factor \(\kappa\) is applied to \(\text{ParamSize}\) to account for the total memory usage on a GPU that stores a fraction of the model, as determined by DP/TP.

---

## 3. Data Parallel Groups

Define \(D\) disjoint DP groups: \(\text{DPGroup}_1,\dots,\text{DPGroup}_D\). For each group \(i\):

- Let \(\text{dp}_{g,i} \in \{0,1\}\) indicate if GPU \(g\) is in DPGroup \(i\).  
- Each GPU can be in at most one DP group:
  \[
    \sum_{i=1}^D \text{dp}_{g,i} \;\le\; 1 
    \quad\text{for each } g.
  \]
- The size of group \(i\) is \(\text{DPSize}_i = \sum_{g} \text{dp}_{g,i}\).  

If fully replicated DP is used, each GPU in DPGroup \(i\) holds the entire model (\(\text{ParamSize}\)). If FSDP (ZeRO-3) is used, each GPU in DPGroup \(i\) holds \(\tfrac{\text{ParamSize}}{\text{DPSize}_i}\), multiplied by \(\kappa\). So the memory usage for DP on GPU \(g\) is:

\[
\text{Mem\_DP}(g,i) 
\;=\; 
\text{dp}_{g,i} 
\;\times\; 
\bigl(\frac{\kappa \,\times\, \text{ParamSize}}{\text{DPSize}_i}\bigr).
\]

We require \(\text{Mem\_DP}(g,i) \;\le\; \text{VRAM}_g\).

---

## 4. Tensor Parallel Groups Within DP Groups

Inside each DP group \(i\), define \(K_i\) disjoint TP groups:

\[
\text{TPGroup}_{i,1}, \ldots, \text{TPGroup}_{i,K_i}.
\]

For each group \((i,j)\):

- Let \(\text{tp}_{g,i,j} \in \{0,1\}\) indicate if GPU \(g\) is in TPGroup \((i,j)\).  
- A GPU \(g\) in DPGroup \(i\) can be in at most one TP group within that DP group:
  \[
    \sum_{j=1}^{K_i} \text{tp}_{g,i,j} 
    \;\le\; 
    1 
    \quad
    \text{for each } g \text{ with } \text{dp}_{g,i} = 1.
  \]
- The size of TPGroup \((i,j)\) is \(\text{TPSize}_{i,j} = \sum_{g} \text{tp}_{g,i,j}\).

If combining DP plus TP with FSDP, each GPU \(g\) in TPGroup \((i,j)\) must hold:

\[
\text{Mem\_TP}(g,i,j) 
= 
\text{tp}_{g,i,j} 
\;\times\;
\bigl(
  \frac{\kappa\,\times\,\text{ParamSize}}
       {\,\text{DPSize}_i \,\times\, \text{TPSize}_{i,j}\!}
\bigr).
\]

We require \(\text{Mem\_TP}(g,i,j) \;\le\; \text{VRAM}_g\). A GPU must also satisfy \(\text{tp}_{g,i,j} \le \text{dp}_{g,i}\) (it can only be in a TP group if it is in the corresponding DP group).

---

## 5. Communication Cost

We define:

1. **Data Parallel Comm** \(\text{dpComm}_i\).  
   - If \(\text{DPSize}_i > 1\), exchanging parameters or gradients among these GPUs might cost \(\text{dpComm}_i \approx \text{DPSize}_i \times \text{ParamSize}\). If using ZeRO-3, we typically see an all-gather or reduce-scatter cost but still increasing with DPSize.

2. **Tensor Parallel Comm** \(\text{tpComm}_{i,j}\).  
   - If \(\text{TPSize}_{i,j} > 1\), partial-sum or cross-attention operations among these GPUs might cost \(\text{tpComm}_{i,j} \approx \text{TPSize}_{i,j} \times \text{ParamSize}\), or a fraction depending on the precise splitting.

We can define a usage variable \(\text{usage\_of}(i,j)\in\{0,1\}\) if only certain groups are actively used. Then total communication might be:

\[
\text{TotalComm} 
= 
\sum_{i=1}^{D} \text{dpComm}_i 
\;+\;
\sum_{i=1}^{D}\sum_{j=1}^{K_i}
  \bigl[\text{usage\_of}(i,j) \times \text{tpComm}_{i,j}\bigr].
\]

---

## 6. MIP Skeleton

**Variables**  
1. \(\text{dp}_{g,i} \in \{0,1\}\) for \(i=1..D\), \(g=1..N\).  
2. \(\text{tp}_{g,i,j} \in \{0,1\}\) for \(i=1..D\), \(j=1..K_i\), \(g=1..N\).  
3. \(\text{usage\_of}(i,j) \in \{0,1\}\) (or \([0,1]\)) if we only choose certain groups to be active.

**Constraints**  
1. **Disjoint DP**:  
   \[
   \sum_{i=1}^{D} \text{dp}_{g,i} \;\le\; 1
   \quad
   \text{for each } g.
   \]
2. **Disjoint TP within each DP**:  
   \[
   \sum_{j=1}^{K_i} \text{tp}_{g,i,j}
   \;\le\;
   1
   \quad
   \text{for each } i,\text{ each } g \text{ with } \text{dp}_{g,i}=1.
   \]
   \[
   \text{tp}_{g,i,j} \;\le\; \text{dp}_{g,i}.
   \]
3. **VRAM feasibility**:  
   - DP: 
     \[
       \text{Mem\_DP}(g,i)
       =
       \text{dp}_{g,i} 
       \times
       \bigl[
         \frac{\kappa \times \text{ParamSize}}{\text{DPSize}_i}
       \bigr]
       \;\;\le\;\;
       \text{VRAM}_g.
     \]
   - TP:
     \[
       \text{Mem\_TP}(g,i,j)
       =
       \text{tp}_{g,i,j}
       \times
       \bigl[
         \frac{\kappa \times \text{ParamSize}}{\text{DPSize}_i \times \text{TPSize}_{i,j}}
       \bigr]
       \;\;\le\;\;
       \text{VRAM}_g.
     \]
4. **Group sizes (optional)**:  
   \(\sum_{g} \text{dp}_{g,i} = \text{DPSize}_i\).  
   \(\sum_{g} \text{tp}_{g,i,j} = \text{TPSize}_{i,j}\).

**Objective**  
\[
\min
\Bigl(
  \sum_{i=1}^{D} \text{dpComm}_i
  +
  \sum_{i=1}^{D}\sum_{j=1}^{K_i}
    [\text{usage\_of}(i,j)\times \text{tpComm}_{i,j}]
\Bigr).
\]

---

## 7. Notes on Heterogeneous VRAM and \(\kappa\)

- **Heterogeneous VRAM**: GPUs with smaller memory must only join DP and TP groups where the fraction \(\tfrac{\kappa \times \text{ParamSize}}{\text{DPSize}_i \times \text{TPSize}_{i,j}}\) fits in \(\text{VRAM}_g\). A larger \(\text{TPSize}_{i,j}\) or \(\text{DPSize}_i\) results in a smaller fraction per GPU, helping smaller VRAM GPUs participate.
- **\(\kappa\) for Adam**: Typically \(\kappa \approx 4\) (params + grads + 2 moments). For simpler optimizers, \(\kappa\) might be around 2 or 3.
- A single GPU belongs to exactly one DP group and at most one TP group in that DP group. This ensures we do not double-count memory usage. 
- In practice, some systems might pick exactly one DP group i and exactly one TP group j inside that i to run a training step. Alternatively, one might allow concurrency or pick from multiple potential groups.

End of Merged Formulation
