import Image from "next/image";

interface BrandMarkProps {
  size?: number;
  className?: string;
  alt?: string;
  priority?: boolean;
}

export function BrandMark({
  size = 28,
  className,
  alt = "ComplianceAI",
  priority = false,
}: BrandMarkProps) {
  return (
    <Image
      src="/brand/logo-256.png"
      alt={alt}
      width={size}
      height={size}
      priority={priority}
      style={{ display: "block", flexShrink: 0 }}
      className={className}
    />
  );
}
