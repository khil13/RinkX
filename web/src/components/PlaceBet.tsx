import { Link } from "react-router";
import type { PropRow } from "../lib/data/types";
import { betFromRow, myBets, useMyBets } from "../lib/myBets";

/** One tap records the bet in My bets at the shown price and book (1 unit). Once placed, the price
 * and stake can be corrected in place (books move), or the entry undone. Stored on this device only. */
export function PlaceBet({ r, compact = false }: { r: PropRow; compact?: boolean }) {
  const bets = useMyBets();
  const placed = bets.find((b) => b.prediction_id === r.prediction_id);
  const bet = betFromRow(r);
  if (!bet) return null;
  if (!placed) {
    return (
      <button
        type="button"
        onClick={() => myBets.add(bet)}
        className={`min-h-9 rounded-md border border-line px-3 hover:border-accent/50 ${compact ? "text-xs" : "text-sm"}`}
      >
        I placed this
      </button>
    );
  }
  return (
    <div className={`flex flex-wrap items-center gap-2 ${compact ? "text-xs" : "text-sm"}`} aria-label="Placed bet">
      <span className="font-semibold text-over">Placed ✓</span>
      {!compact && (
        <>
          <label className="flex items-center gap-1 text-xs text-muted">
            Price
            <input
              type="number"
              inputMode="numeric"
              defaultValue={placed.price}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (Math.abs(v) >= 100) myBets.update(placed.id, { price: v });
              }}
              className="num min-h-9 w-20 rounded border border-line bg-panel px-2 text-text"
            />
          </label>
          <label className="flex items-center gap-1 text-xs text-muted">
            Units
            <input
              type="number"
              inputMode="decimal"
              step="0.25"
              defaultValue={placed.stake}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (v > 0) myBets.update(placed.id, { stake: v });
              }}
              className="num min-h-9 w-16 rounded border border-line bg-panel px-2 text-text"
            />
          </label>
        </>
      )}
      <button type="button" onClick={() => myBets.remove(placed.id)} className="min-h-9 text-xs text-muted underline">
        Undo
      </button>
      {!compact && (
        <Link to="/my" className="text-xs text-accent underline">
          My bets
        </Link>
      )}
    </div>
  );
}
