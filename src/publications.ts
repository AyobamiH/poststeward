import twitterText from "twitter-text";
import { digest, Fault, requireValue } from "./common.ts";
import { validateText } from "./providers.ts";
import type { Provider } from "./types.ts";

export interface PublicationPart {
  index: number;
  text: string;
  digest: string;
}

export interface FrozenPublication {
  type: "single" | "thread";
  text: string;
  digest: string;
  publicationDigest: string;
  parts: PublicationPart[];
}

const PART_LIMITS: Partial<Record<Provider, number>> = {
  x: 275,
  threads: 500,
};
const MAX_PARTS = 25;
const X_WEIGHT_ONE_RANGES: Array<[number, number]> = [
  [0, 4351],
  [8192, 8205],
  [8208, 8223],
  [8242, 8247],
];
const X_URL = /\b(?:https?:\/\/|www\.)\S+/giu;

function xWeight(character: string) {
  const value = character.codePointAt(0)!;
  return X_WEIGHT_ONE_RANGES.some(
    ([start, end]) => value >= start && value <= end,
  )
    ? 1
    : 2;
}

/** Return a UTF-16 boundary whose prefix fits X's transformed URL weighting. */
function xMaximumPrefix(text: string, limit: number) {
  let total = 0;
  let cursor = 0;
  X_URL.lastIndex = 0;
  for (let match = X_URL.exec(text); match; match = X_URL.exec(text)) {
    let offset = cursor;
    for (const character of text.slice(cursor, match.index)) {
      if (total + xWeight(character) > limit) return offset;
      total += xWeight(character);
      offset += character.length;
    }
    if (total + 23 > limit) return match.index;
    total += 23;
    cursor = match.index + match[0].length;
  }
  let offset = cursor;
  for (const character of text.slice(cursor)) {
    if (total + xWeight(character) > limit) return offset;
    total += xWeight(character);
    offset += character.length;
  }
  return text.length;
}

function codePointMaximumPrefix(text: string, limit: number) {
  let length = 0;
  let offset = 0;
  for (const character of text) {
    if (length === limit) break;
    length++;
    offset += character.length;
  }
  return offset;
}

function preferredCut(text: string, maximum: number) {
  const window = text.slice(0, maximum + 1);
  const floor = Math.max(1, Math.floor(maximum * 0.45));
  const boundaries = ["\n\n", "\n", ". ", "? ", "! ", "; ", ": ", ", ", " "];
  for (const token of boundaries) {
    const index = window.lastIndexOf(token);
    if (index >= floor)
      return index + (token.trim().length ? token.trimEnd().length : 0);
  }
  const space = window.lastIndexOf(" ");
  return space > 0 ? space : maximum;
}

function split(provider: Provider, value: string) {
  const limit = PART_LIMITS[provider];
  if (!limit) return [value];
  const parts: string[] = [];
  let remaining = value;
  const fits = (text: string) =>
    provider === "x"
      ? twitterText.parseTweet(text).weightedLength <= limit &&
        twitterText.parseTweet(text).valid
      : [...text].length <= limit;
  while (!fits(remaining)) {
    const maximum =
      provider === "x"
        ? xMaximumPrefix(remaining, limit)
        : codePointMaximumPrefix(remaining, limit);
    requireValue(
      maximum > 0,
      "CONTENT_PART_INVALID",
      `${provider} content contains a token that cannot fit one post.`,
    );
    let cut = preferredCut(remaining, maximum);
    let part = remaining.slice(0, cut).trim();
    if (!part || !fits(part)) {
      cut = maximum;
      part = remaining.slice(0, cut).trim();
    }
    requireValue(
      Boolean(part) && fits(part),
      "CONTENT_PART_INVALID",
      `${provider} content could not be split within provider limits.`,
    );
    parts.push(part);
    requireValue(
      parts.length < MAX_PARTS,
      "CONTENT_TOO_LONG",
      `${provider} content exceeds the ${MAX_PARTS}-part safety limit.`,
    );
    remaining = remaining.slice(cut).trimStart();
  }
  if (remaining) parts.push(remaining);
  return parts;
}

export function publicationParts(provider: Provider, text: string) {
  const value = String(text).replaceAll("\r\n", "\n").trim();
  requireValue(value.length > 0, "EMPTY_CONTENT", "Content cannot be blank.");
  if (provider === "linkedin") {
    validateText(provider, value);
    return [value];
  }
  const parts = split(provider, value);
  requireValue(
    parts.length <= MAX_PARTS,
    "CONTENT_TOO_LONG",
    `${provider} content exceeds the ${MAX_PARTS}-part safety limit.`,
  );
  for (const part of parts) validateText(provider, part);
  return parts;
}

export async function freezePublication(
  provider: Provider,
  text: string,
): Promise<FrozenPublication> {
  const normalized = String(text).replaceAll("\r\n", "\n").trim();
  const texts = publicationParts(provider, normalized);
  const parts = await Promise.all(
    texts.map(async (part, offset) => ({
      index: offset + 1,
      text: part,
      digest: await digest(part),
    })),
  );
  const type = parts.length > 1 ? "thread" : "single";
  const contentDigest = await digest(normalized);
  return {
    type,
    text: normalized,
    digest: contentDigest,
    publicationDigest: await digest({
      provider,
      type,
      digest: contentDigest,
      parts: parts.map(({ index, digest }) => ({ index, digest })),
    }),
    parts,
  };
}

export async function validateFrozenPublication(
  provider: Provider,
  publication: FrozenPublication,
) {
  let rebuilt: FrozenPublication;
  try {
    rebuilt = await freezePublication(provider, publication.text);
  } catch (error) {
    if (error instanceof Fault) throw error;
    throw new Fault(
      "PAYLOAD_DRIFT",
      "Captured publication could not be reconstructed.",
      409,
    );
  }
  requireValue(
    rebuilt.digest === publication.digest &&
      rebuilt.type === publication.type &&
      rebuilt.publicationDigest === publication.publicationDigest &&
      JSON.stringify(rebuilt.parts) === JSON.stringify(publication.parts),
    "PAYLOAD_DRIFT",
    "Captured publication integrity failed.",
    409,
  );
  return rebuilt;
}
