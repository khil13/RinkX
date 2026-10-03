// NHL team colours (primary, secondary), keyed by the NHL's three-letter abbreviation. Colours
// only: no logos or marks. Text on a colour is black or white, whichever contrasts more.

export const TEAM_COLORS: Record<string, [string, string]> = {
  ANA: ["#F47A38", "#B9975B"],
  BOS: ["#FFB81C", "#000000"],
  BUF: ["#003087", "#FFB81C"],
  CAR: ["#CE1126", "#000000"],
  CBJ: ["#002654", "#CE1126"],
  CGY: ["#C8102E", "#F1BE48"],
  CHI: ["#CF0A2C", "#000000"],
  COL: ["#6F263D", "#236192"],
  DAL: ["#006847", "#8F8F8C"],
  DET: ["#CE1126", "#FFFFFF"],
  EDM: ["#041E42", "#FF4C00"],
  FLA: ["#C8102E", "#041E42"],
  LAK: ["#111111", "#A2AAAD"],
  MIN: ["#154734", "#A6192E"],
  MTL: ["#AF1E2D", "#192168"],
  NJD: ["#CE1126", "#000000"],
  NSH: ["#FFB81C", "#041E42"],
  NYI: ["#00539B", "#F47D30"],
  NYR: ["#0038A8", "#CE1126"],
  OTT: ["#C52032", "#C2912C"],
  PHI: ["#F74902", "#000000"],
  PIT: ["#FCB514", "#000000"],
  SEA: ["#001628", "#99D9D9"],
  SJS: ["#006D75", "#EA7200"],
  STL: ["#002F87", "#FCB514"],
  TBL: ["#002868", "#FFFFFF"],
  TOR: ["#00205B", "#FFFFFF"],
  UTA: ["#69B3E7", "#010101"],
  VAN: ["#00205B", "#00843D"],
  VGK: ["#B4975A", "#333F42"],
  WSH: ["#C8102E", "#041E42"],
  WPG: ["#041E42", "#004C97"],
};

const NEUTRAL: [string, string] = ["#3A4556", "#8A96A8"]; // unknown teams (e.g. test data)

function luminance(hex: string): number {
  const v = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = v.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lin[0]! + 0.7152 * lin[1]! + 0.0722 * lin[2]!;
}

export function contrastText(hex: string): "#000000" | "#FFFFFF" {
  const l = luminance(hex);
  // WCAG contrast against black (l + 0.05) / 0.05 vs white 1.05 / (l + 0.05): pick the larger.
  return (l + 0.05) / 0.05 >= 1.05 / (l + 0.05) ? "#000000" : "#FFFFFF";
}

export function teamColors(abbrev: string | null | undefined): { primary: string; secondary: string; text: string } {
  const [primary, secondary] = (abbrev && TEAM_COLORS[abbrev]) || NEUTRAL;
  return { primary, secondary, text: contrastText(primary) };
}
