import { cn } from "@/lib/utils";
import { ButtonHTMLAttributes, forwardRef } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(
          // Base
          "inline-flex items-center justify-center gap-2 rounded-md font-medium tracking-tight",
          "transition-all duration-150 cursor-pointer select-none",
          "focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-2",
          "disabled:pointer-events-none disabled:opacity-35",
          "active:scale-[0.97]",
          // Variants
          variant === "primary" &&
            "bg-brand text-[#0c0c0e] hover:bg-brand-light",
          variant === "secondary" &&
            "bg-surface-high text-foreground border border-border hover:bg-surface-hover hover:border-border-strong",
          variant === "ghost" &&
            "text-muted hover:text-foreground hover:bg-surface-high",
          variant === "danger" &&
            "bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 hover:border-red-500/40",
          // Sizes
          size === "sm" && "px-3 py-1.5 text-xs",
          size === "md" && "px-3.5 py-2 text-sm",
          size === "lg" && "px-5 py-2.5 text-sm",
          className
        )}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button };
