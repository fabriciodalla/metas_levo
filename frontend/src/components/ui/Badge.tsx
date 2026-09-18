import type { ReactNode } from "react";

type Variant = "success" | "danger" | "warning" | "neutral" | "accent";

export function Badge({
  variant = "neutral",
  icon,
  children,
}: {
  variant?: Variant;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <span className={`badge badge-${variant}`}>
      {icon}
      {children}
    </span>
  );
}
