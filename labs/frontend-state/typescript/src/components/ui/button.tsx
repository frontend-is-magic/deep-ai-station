import { Slot } from '@radix-ui/react-slot';
import { cva } from 'class-variance-authority';
import type { ButtonHTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

const style = cva(
  'min-h-11 whitespace-normal rounded-lg bg-lime-200 px-4 py-2 font-medium text-stone-900 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lime-700',
);
export function Button({
  asChild = false,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { asChild?: boolean }) {
  const Component = asChild ? Slot : 'button';
  return <Component className={cn(style(), className)} {...props} />;
}
