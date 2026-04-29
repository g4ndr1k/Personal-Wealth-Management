# Plan — Upgrade `/tokenizer` to a tiktokenizer-style playground

## Context
`https://codingholic.fun/tokenizer` already exists. It's a minimal "Token Counter" — one model dropdown (3 entries, all `cl100k_base`), one textarea, one big number, char/word counts. No chat-message editor, no per-token visualization, no token IDs, no copy buttons, no encodings beyond `cl100k_base`.

The user wants parity with [tiktokenizer.vercel.app](https://tiktokenizer.vercel.app/): a two-pane playground where the left side composes the input (plain textarea **or** structured chat messages depending on the model), and the right side renders the resulting prompt as colored token chunks with the integer token IDs underneath. Same client-side, no-backend posture as today.

`js-tiktoken@1.0.21` is **already** in `package.json` on the NAS — no new dependency required.

### Where the code lives
The repo is **on the NAS**, not in `~/projects/codingholic-homepage`. Per `ch-hp-worklow.md`:
- Edit at `/Volumes/docker/codingholic-homepage/stag/` (Mac SMB mount of the NAS).
- The mount must be live before editing (currently it is **not** mounted — `/Volumes/docker` does not exist on this Mac). User needs to `Finder → Connect to Server → smb://192.168.1.44 → docker` first.
- Deploy: `~/deploy-codingholic.sh` (rebuilds `codingholic-homepage-stag` on port 3003 → `staging.codingholic.fun`).
- Promote: `~/promote-codingholic.sh` (rsync stag→prod, rebuild `codingholic-homepage-prod` on 3002 → `codingholic.fun`).

### Files in the existing tokenizer (NAS paths)
- `app/tokenizer/page.tsx` — server shell (back link, h1 "Token Counter", `<TokenizerClient />`). **Rename heading to "Tokenizer"** to match the new scope.
- `app/components/tokenizer-client.tsx` — `next/dynamic` SSR-disabled wrapper. Keep as-is.
- `app/components/tokenizer.tsx` — the whole client component. **This is the file that gets the bulk of the rewrite.**
- `app/lib/content.ts` — tool card title is "Token Counter". Update title + summary to reflect the broader feature set.

## Approach

Rewrite `app/components/tokenizer.tsx` into a two-pane layout, and split the heaviest pieces into sibling files for readability. Keep the `TokenizerClient` dynamic-import wrapper untouched (preserves the existing loading state and SSR-off behavior).

### Files to add (under `app/components/tokenizer/`)
- `models.ts` — model registry. Each entry: `{ id, label, encoding, mode: "chat" | "completion" }`. Initial set:
  - Chat: `gpt-4o` (`o200k_base`), `gpt-4o-mini` (`o200k_base`), `gpt-4-turbo` (`cl100k_base`), `gpt-4` (`cl100k_base`), `gpt-3.5-turbo` (`cl100k_base`).
  - Completion: `text-davinci-003` (`p50k_base`), `davinci` (`r50k_base`), `gpt2` (`gpt2`).
  - Raw encodings: `cl100k_base`, `o200k_base`, `p50k_base`, `r50k_base` (mode `completion`, no ChatML framing).
- `chat-template.ts` — given `(modelId, messages)` returns the formatted prompt string with the ChatML framing tiktokenizer uses: `<|im_start|>role\ncontent<|im_end|>\n…<|im_start|>assistant\n`. Encode with `enc.encode(prompt, "all")` (allow special tokens) so `<|im_start|>` etc. count as single tokens — matches tiktokenizer.
- `chat-editor.tsx` — left pane for chat mode. Renders rows of `{role, content}` with a role dropdown (`system`/`user`/`assistant`/`tool`), a textarea, a delete button per row, and an "Add message" button. Default seed: `[{system: "You are a helpful assistant"}, {user: ""}]`.
- `text-editor.tsx` — left pane for completion mode. Single textarea (lifted from current implementation, plus the existing Paste/Clear buttons).
- `token-display.tsx` — right pane. Renders the `number[]` from `enc.encode(...)`:
  - Header: big token count + char count badges.
  - Body: each token as an inline span with cycling Tailwind tints (`bg-pink-500/30`, `bg-amber-500/30`, `bg-emerald-500/30`, `bg-cyan-500/30`, `bg-violet-500/30`, `bg-rose-500/30`); whitespace rendered as a visible `·`, newlines as `↵\n`.
  - Toggle "Show token IDs" → comma-separated `[123, 456, …]` with a "Copy" button.
  - "Copy formatted prompt" button.
- (Optional) `index.ts` barrel for cleaner imports.

### Files to modify
- `app/components/tokenizer.tsx` — becomes the orchestrator: holds `selectedModelId`, `chatMessages`, `completionText`, `showIds` state; lazy-loads the encoder for the selected model's encoding (cache encoders in a `Map<encoding, Tiktoken>` ref so switching models doesn't re-fetch the BPE table); renders `<ChatEditor>` or `<TextEditor>` on the left and `<TokenDisplay>` on the right. Two-column on `md:` and up, stacked below.
- `app/tokenizer/page.tsx` — change `<h1>Token Counter</h1>` to `<h1>Tokenizer</h1>` and update the subtitle to: "See exactly how GPT models split your text into tokens. Runs locally — your text never leaves the browser."
- `app/lib/content.ts` — rename the tool card from "Token Counter" → "Tokenizer", and update its `summary` to mention chat-template support and per-token visualization.

### Encoder handling notes
- `getEncoding(name)` in `js-tiktoken` is synchronous once imported, but the dynamic import payload is per-encoding. Keep the existing `dynamic(() => import("./tokenizer"), { ssr: false })` wrapper, then inside the component lazy-import `js-tiktoken` once and call `getEncoding(currentEncoding)` on demand, caching results in a `useRef<Map<string, ReturnType<typeof getEncoding>>>`.
- Special-token handling: for chat mode the formatted ChatML string includes `<|im_start|>` / `<|im_end|>` literally. `js-tiktoken`'s `encode(text, allowedSpecial)` accepts an array of allowed special tokens (or `"all"`); pass `"all"` so they encode as single token IDs (tiktokenizer behavior). For completion mode pass `[]` (default) so user text containing `<|endoftext|>` etc. does **not** silently get treated as a special token.
- Use `encoder.decode([id])` per token to render chunks. For `o200k_base`/`cl100k_base` this can produce invalid UTF-8 mid-token (multi-byte chars split across two BPE tokens) — `js-tiktoken`'s `decode` handles partial bytes by replacement, which is acceptable and matches tiktokenizer's display.

### Behavior parity checklist
- Switching from a chat model to a completion model preserves the typed text where reasonable (concatenate chat contents into the completion textarea on switch; vice versa stuff the textarea into a single `user` message). Document the one-way nature in a small note rather than building bidirectional sync.
- Chat-model token count includes the trailing `<|im_start|>assistant\n` priming tokens (same as tiktokenizer).
- Token recompute is debounced 150ms via the existing pattern in `tokenizer.tsx` (already 200ms — fine to keep).
- "Show token IDs" off by default (matches tiktokenizer).
- Mobile: panes stack, chat-row controls remain tappable.

### Out of scope (defer; leave a disabled "coming soon" entry)
- Claude / Llama / Mistral tokenizers — those need HuggingFace `tokenizer.json` + `@huggingface/transformers`, ~MB extra bundle and CORS handling. Add greyed-out entries in the model dropdown so users see they're planned.
- Persisting state across reloads (URL/localStorage).
- SSR of token output.

## Verification (after editing on the NAS mount)
1. **Local sanity** — on the Mac, run a quick build against the mount to catch TS errors before touching the NAS Docker:
   ```bash
   cd /Volumes/docker/codingholic-homepage/stag
   npm install   # only if package.json changed (it shouldn't)
   npm run build
   ```
2. **Deploy stag**: `~/deploy-codingholic.sh` → open `https://staging.codingholic.fun/tokenizer`.
3. **Functional checks** vs. tiktokenizer.vercel.app side-by-side:
   - `cl100k_base` raw, input `"hello world"` → 2 tokens `[15339, 1917]`.
   - `gpt-4o` chat with system `"You are helpful."` + user `"Hi"` → token count must equal tiktokenizer's count exactly (validates ChatML framing + assistant priming).
   - `o200k_base` raw long paste (~5k chars) → no jank, count stable.
   - Toggle "Show token IDs", copy buttons work, model switching preserves content per the rule above.
   - Chat editor: add/remove rows, change roles, delete the last row gracefully.
4. **Mobile** — resize browser to ~375px or hit it from phone on Tailscale: panes stack, no horizontal scroll.
5. **Cards page** — `https://staging.codingholic.fun/` shows the renamed "Tokenizer" card linking to `/tokenizer`.
6. **Promote**: `~/promote-codingholic.sh` → confirm `https://codingholic.fun/tokenizer` renders the new UI.

## Critical files
- New: `app/components/tokenizer/models.ts`, `chat-template.ts`, `chat-editor.tsx`, `text-editor.tsx`, `token-display.tsx`.
- Modified: `app/components/tokenizer.tsx` (rewrite into orchestrator), `app/tokenizer/page.tsx` (heading + subtitle), `app/lib/content.ts` (tool card label + summary).
- Untouched: `app/components/tokenizer-client.tsx` (dynamic-import wrapper), `package.json` (`js-tiktoken` already pinned).

## Pre-flight reminder
The Mac SMB mount at `/Volumes/docker/codingholic-homepage/stag` is currently **not mounted** on this machine. Before editing: `Finder → Go → Connect to Server → smb://192.168.1.44 → docker`, and verify `ls /Volumes/docker/codingholic-homepage/stag/app/tokenizer/` returns `page.tsx`.
