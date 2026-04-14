import React, { useState, useRef, useLayoutEffect, cloneElement } from 'react';

type NavItem = {
  id: string | number;
  icon: React.ReactElement;
  label?: string;
  onClick?: () => void;
};

type LimelightNavProps = {
  items?: NavItem[];
  defaultActiveIndex?: number;
  onTabChange?: (index: number) => void;
  className?: string;
  limelightClassName?: string;
  iconContainerClassName?: string;
  iconClassName?: string;
};

const LimelightNav = ({
  items = [],
  defaultActiveIndex = 0,
  onTabChange,
  className,
  limelightClassName,
  iconContainerClassName,
  iconClassName,
}: LimelightNavProps) => {
  const [activeIndex, setActiveIndex] = useState(defaultActiveIndex);
  const [isReady, setIsReady] = useState(false);
  const navItemRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  const limelightRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (items.length === 0) return;

    const limelight = limelightRef.current;
    const activeItem = navItemRefs.current[activeIndex];

    if (limelight && activeItem) {
      const newLeft = activeItem.offsetLeft + activeItem.offsetWidth / 2 - limelight.offsetWidth / 2;
      limelight.style.left = `${newLeft}px`;

      if (!isReady) {
        setTimeout(() => setIsReady(true), 50);
      }
    }
  }, [activeIndex, isReady, items]);

  if (items.length === 0) return null;

  const handleItemClick = (index: number, itemOnClick?: () => void) => {
    setActiveIndex(index);
    onTabChange?.(index);
    itemOnClick?.();
  };

  return (
    <nav className={`relative inline-flex items-center h-14 rounded-2xl px-1 ${className}`}>
      {items.map(({ id, icon, label, onClick }, index) => (
        <a
          key={id}
          ref={(el: HTMLAnchorElement | null) => { navItemRefs.current[index] = el; }}
          className={`relative z-20 flex h-full cursor-pointer items-center justify-center px-4 ${iconContainerClassName}`}
          onClick={() => handleItemClick(index, onClick)}
          aria-label={label}
        >
          {cloneElement(icon, {
            className: `w-5 h-5 transition-all duration-200 ease-out ${
              activeIndex === index ? 'opacity-100 scale-110' : 'opacity-35 scale-100'
            } ${icon.props.className || ''} ${iconClassName || ''}`,
          })}
        </a>
      ))}

      <div
        ref={limelightRef}
        className={`absolute bottom-0 z-10 w-10 h-[4px] rounded-full ${
          isReady ? 'transition-[left] duration-300 ease-out' : ''
        } ${limelightClassName}`}
        style={{ left: '-999px', background: 'var(--mc-accent)', boxShadow: '0 -8px 20px var(--mc-accent), 0 -2px 8px var(--mc-accent)' }}
      >
        <div
          className="absolute left-[-25%] bottom-[3px] w-[150%] h-12 pointer-events-none"
          style={{
            clipPath: 'polygon(5% 0%, 25% 100%, 75% 100%, 95% 0%)',
            background: 'linear-gradient(to top, color-mix(in srgb, var(--mc-accent) 25%, transparent), transparent)',
          }}
        />
      </div>
    </nav>
  );
};

export { LimelightNav };
export type { NavItem, LimelightNavProps };
