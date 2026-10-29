"use client";

import { forwardRef, useEffect, useId, useRef, useState } from "react";
import { suggestPlaces, type PlaceSuggestion } from "@/lib/types";

/**
 * Type-ahead for US cities and airports. The parent only accepts a value picked from the
 * list (`onPick`), so a typo like "Danton" can't reach the assessment.
 */
export const PlaceInput = forwardRef<
  HTMLInputElement,
  {
    label: string;
    value: string;
    confirmed: boolean;
    error: string | null;
    onType: (text: string) => void;
    onPick: (value: string, timezone?: string | null) => void;
  }
>(function PlaceInput({ label, value, confirmed, error, onType, onPick }, ref) {
  const id = useId();
  const [items, setItems] = useState<PlaceSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const latest = useRef(0);

  // Fetch suggestions as the user types (debounced; stale responses are dropped).
  useEffect(() => {
    if (confirmed || value.trim().length < 2) {
      setItems([]);
      setLoading(false);
      return;
    }
    const req = ++latest.current;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const res = await suggestPlaces(value);
        if (req !== latest.current) return;
        setItems(res);
        setActive(res.length ? 0 : -1);
        setFailed(false);
      } catch {
        if (req === latest.current) setFailed(true);
      } finally {
        if (req === latest.current) setLoading(false);
      }
    }, 150);
    return () => clearTimeout(t);
  }, [value, confirmed]);

  function pick(s: PlaceSuggestion) {
    onPick(s.value, s.timezone);
    setOpen(false);
    setItems([]);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      if (!items.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((a) => (a + step + items.length) % items.length);
    } else if (e.key === "Enter" && open && active >= 0 && items[active]) {
      e.preventDefault();
      pick(items[active]);
    } else if (e.key === "Tab" && open && !confirmed && active >= 0 && items[active]) {
      pick(items[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  function onBlur() {
    setOpen(false);
    // Typed the full name of a suggestion exactly (e.g. "dfw" or "denton, tx"): accept it.
    const exact = items.find((s) => s.value.toLowerCase() === value.trim().toLowerCase());
    if (!confirmed && exact) pick(exact);
  }

  const showList = open && !confirmed && value.trim().length >= 2;
  const listId = `${id}-list`;
  const hintId = `${id}-hint`;

  return (
    <div className="field field-place">
      <label htmlFor={id}>{label}</label>
      <div className="combo">
        <input
          ref={ref}
          id={id}
          required
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList && active >= 0 ? `${id}-opt-${active}` : undefined}
          aria-invalid={!!error}
          aria-describedby={error ? hintId : undefined}
          className={confirmed ? "picked" : undefined}
          value={value}
          onChange={(e) => {
            onType(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          placeholder="Start typing a city or airport"
          autoComplete="off"
          spellCheck={false}
        />
        {showList && (
          <ul className="combo-list" id={listId} role="listbox" aria-label={`${label} suggestions`}>
            {items.map((s, i) => (
              <li
                key={`${s.kind}-${s.value}`}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={i === active}
                className={i === active ? "active" : undefined}
                onMouseDown={(e) => e.preventDefault()} // keep focus so blur doesn't close first
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(s)}
              >
                <span className="combo-label">{s.label}</span>
                <span className="combo-detail">
                  {s.kind === "airport" ? "Airport · " : ""}
                  {s.detail}
                </span>
              </li>
            ))}
            {!items.length && (
              <li className="combo-empty" aria-disabled="true">
                {loading ? "Searching…" : failed ? "Suggestions unavailable. Is the backend running?" : "No US city or airport matches that."}
              </li>
            )}
          </ul>
        )}
      </div>
      {error && (
        <span className="field-error" id={hintId}>
          {error}
        </span>
      )}
    </div>
  );
});
