// Client-side fallback for mapping an LLM-cited evidence quote to a
// precise timestamp when the backend hasn't computed `cp.start_ms` yet
// (e.g., calls analyzed before the find_word_range pipeline change).
// Mirrors backend/app/word_match.py — same token rules, same sliding-
// window density algorithm. Runs against the AssemblyAI word stream the
// karaoke player already fetches, so no extra network cost.
//
// Algorithm: sliding window sized ~1.8× the needle token count. For each
// window position, count DISTINCT needle tokens present. Pick the window
// with the highest count (ties break on narrower span). Trims leading/
// trailing non-matching words so the returned range brackets only the
// matched region.
//
// Ported verbatim from `frontend/src/lib/word-match.ts` (main branch). The
// local `MinimalWord` shape avoids coupling to a specific WordData /
// WordToken type — any object with `{word, start}` (start in seconds) works.

export interface MinimalWord {
  word: string;
  start: number; // seconds
  end: number;   // seconds
}

const MIN_TOKEN_LENGTH = 3;
const MIN_OVERLAP_RATIO = 0.40;
const WINDOW_SCALE = 1.8;
const MIN_WINDOW_WORDS = 8;

const QUOTE_CHARS = new Set("\"'“”‘’„‚«»`".split(""));
const SPEAKER_PREFIX = /^\s*(agent|customer|speaker [ab]|[ab])(\s+said)?\s*:\s*/i;

function normalize(text: string): string[] {
  const cleaned = text.trim().replace(SPEAKER_PREFIX, "");
  const stripped = Array.from(cleaned).filter((ch) => !QUOTE_CHARS.has(ch)).join("");
  const tokens = stripped.toLowerCase().match(/[a-z0-9]+/g) || [];
  return tokens.filter((t) => t.length >= MIN_TOKEN_LENGTH);
}

export function findWordRangeMs(
  evidence: string | null | undefined,
  words: MinimalWord[] | null | undefined,
): [number | null, number | null] {
  if (!evidence || !evidence.trim() || !words || words.length === 0) {
    return [null, null];
  }

  const needle = normalize(evidence);
  if (needle.length === 0) return [null, null];
  const needleSet = new Set(needle);

  // Pre-compute each word's significant tokens so the sliding window
  // doesn't re-normalize the same word on every window slide.
  const wordTokenSets: Set<string>[] = words.map((w) => new Set(normalize(w.word || "")));

  let windowSize = Math.max(MIN_WINDOW_WORDS, Math.floor(needle.length * WINDOW_SCALE));
  if (windowSize > words.length) windowSize = words.length;

  let bestCoverage = 0;
  let bestSpanLen = words.length + 1;
  let bestSpan: [number, number] | null = null;

  for (let i = 0; i <= words.length - windowSize; i++) {
    const covered = new Set<string>();
    for (let j = i; j < i + windowSize; j++) {
      for (const tok of wordTokenSets[j]) {
        if (needleSet.has(tok)) covered.add(tok);
      }
    }
    if (covered.size === 0) continue;

    let left = i;
    let right = i + windowSize - 1;
    while (left <= right && !hasAnyMatch(wordTokenSets[left], needleSet)) left++;
    while (right >= left && !hasAnyMatch(wordTokenSets[right], needleSet)) right--;
    const spanLen = right - left + 1;

    if (covered.size > bestCoverage || (covered.size === bestCoverage && spanLen < bestSpanLen)) {
      bestCoverage = covered.size;
      bestSpan = [left, right];
      bestSpanLen = spanLen;
    }
  }

  if (bestSpan === null) return [null, null];
  if (bestCoverage / needleSet.size < MIN_OVERLAP_RATIO) return [null, null];

  const [firstIdx, lastIdx] = bestSpan;
  return [
    Math.round(words[firstIdx].start * 1000),
    Math.round(words[lastIdx].end * 1000),
  ];
}

function hasAnyMatch(wordTokens: Set<string>, needleSet: Set<string>): boolean {
  for (const tok of wordTokens) {
    if (needleSet.has(tok)) return true;
  }
  return false;
}
