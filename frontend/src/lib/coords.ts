// Parsing coordinates typed by people. RA accepts decimal degrees ("133.7951") and sexagesimal
// hours ("08h55m10.831s", "8:55:10.831", "8 55 10.831"). Dec accepts decimal degrees ("-7.2452")
// and sexagesimal degrees ("-7d14m42.53s", "-07:14:42.5", "-7 14 42.5").
//
// A bare number is degrees, for both. Anything with 2 or 3 parts, or an 'h', is hours for RA
// unless it carries a 'd' or a degree sign.

export type Parsed = { ok: true; value: number } | { ok: false; error: string };

const NUMBER = /^\d+(\.\d+)?$/;
const SEPARATORS = /[hdms°'"′″:]/gi;

interface Split {
  sign: 1 | -1;
  parts: number[];
  hours: boolean;
  degrees: boolean;
}

function split(text: string): Split | string {
  const t = text.trim();
  if (!t) return "Enter a value.";
  const sign = t.startsWith("-") ? -1 : 1;
  const body = t.replace(/^[+-]/, "");
  const parts = body.replace(SEPARATORS, " ").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0 || parts.length > 3 || !parts.every((p) => NUMBER.test(p))) {
    return "Could not read that. Use degrees (133.7951) or hours/degrees, minutes, seconds.";
  }
  const nums = parts.map(Number);
  if (nums.slice(1).some((n) => n >= 60)) return "Minutes and seconds must be below 60.";
  return {
    sign,
    parts: nums,
    hours: /h/i.test(body),
    degrees: /[d°]/i.test(body),
  };
}

function combine(parts: number[]): number {
  const [a = 0, b = 0, c = 0] = parts;
  return a + b / 60 + c / 3600;
}

export function parseRa(text: string): Parsed {
  const s = split(text);
  if (typeof s === "string") return { ok: false, error: s };
  if (s.sign < 0) return { ok: false, error: "RA cannot be negative (0 to 360 degrees)." };
  const sexagesimal = s.parts.length > 1 || s.hours;
  const value = sexagesimal && !s.degrees ? combine(s.parts) * 15 : combine(s.parts);
  if (!(value >= 0 && value < 360)) {
    return { ok: false, error: "RA must be from 0 up to 360 degrees (0 to 24 hours)." };
  }
  return { ok: true, value };
}

export function parseDec(text: string): Parsed {
  const s = split(text);
  if (typeof s === "string") return { ok: false, error: s };
  const value = s.sign * combine(s.parts);
  if (!(value >= -90 && value <= 90)) {
    return { ok: false, error: "Dec must be between -90 and +90 degrees." };
  }
  return { ok: true, value };
}
