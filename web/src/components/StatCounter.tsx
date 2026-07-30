"use client";

import { useEffect, useRef, useState } from "react";

function useCounter(target: number, duration = 1800, active = false) {
  const [value, setValue] = useState(0);
  const raf = useRef<number>(0);

  useEffect(() => {
    if (!active) return;
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      // ease-out-expo
      const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      setValue(Math.round(eased * target));
      if (progress < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, duration, active]);

  return value;
}

interface StatProps {
  target: number;
  suffix?: string;
  label: string;
  delay?: number;
}

function Stat({ target, suffix = "", label, delay = 0 }: StatProps) {
  const [active, setActive] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setActive(true), delay);
    return () => clearTimeout(t);
  }, [delay]);

  const count = useCounter(target, 1800, active);

  return (
    <div
      className="text-center"
      style={{
        animation: "fade-up 0.5s cubic-bezier(0.16,1,0.3,1) both",
        animationDelay: `${delay}ms`,
      }}
    >
      <p className="text-[36px] font-bold tracking-tight text-foreground tabular-nums leading-none">
        {count.toLocaleString()}
        <span className="text-brand">{suffix}</span>
      </p>
      <p className="mt-2 text-[12px] text-muted">{label}</p>
    </div>
  );
}

export function StatCounter() {
  return (
    <div className="grid grid-cols-3 gap-4 py-10">
      <Stat target={1247} suffix="+" label="actions detected"  delay={0}   />
      <Stat target={312}  suffix="+" label="clips generated"   delay={120} />
      <Stat target={28}   suffix=""  label="games analyzed"    delay={240} />
    </div>
  );
}
