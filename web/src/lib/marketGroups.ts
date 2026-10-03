// Player and goalie markets grouped the way the app shows them (Today, game prop center, Card of the Day).
export const GROUPS: { key: string; title: string; markets: string[] }[] = [
  { key: "sog", title: "🔥 Best SOG props", markets: ["skater_shots_on_goal"] },
  { key: "points", title: "🔥 Best point props", markets: ["skater_points", "skater_pp_points"] },
  { key: "goals", title: "🔥 Best goal props", markets: ["skater_goals", "skater_anytime_goal", "skater_first_goal", "skater_pp_goal"] },
  { key: "assists", title: "🔥 Best assist props", markets: ["skater_assists", "skater_pp_assist"] },
  { key: "blocks", title: "🔥 Best block props", markets: ["skater_blocked_shots"] },
  { key: "hits", title: "🔥 Best hit props", markets: ["skater_hits"] },
  { key: "goalie", title: "🔥 Best goalie props", markets: ["goalie_saves", "goalie_goals_against", "goalie_win", "goalie_shutout", "goalie_saves_and_win"] },
];
