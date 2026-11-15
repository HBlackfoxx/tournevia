import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ask, ConfirmHost } from './confirm';

it('asks in its own dialog, keeps focus on the safe answer and returns it afterwards', async () => {
  render(
    <>
      <button>Leave</button>
      <ConfirmHost />
    </>,
  );
  const leave = screen.getByRole('button', { name: 'Leave' });
  leave.focus();
  let answer!: Promise<boolean>;
  act(() => {
    answer = ask('Discard unsaved record changes?', { detail: 'The new values are lost.' });
  });
  const dialog = screen.getByRole('alertdialog', { name: 'Discard unsaved record changes?' });
  expect(dialog).toHaveAccessibleDescription('The new values are lost.');
  const keep = within(dialog).getByRole('button', { name: 'Keep editing' }),
    discard = within(dialog).getByRole('button', { name: 'Discard changes' });
  expect(keep).toHaveFocus();
  fireEvent.keyDown(keep, { key: 'Tab' });
  expect(discard).toHaveFocus();
  fireEvent.keyDown(discard, { key: 'Tab' });
  expect(keep).toHaveFocus();
  fireEvent.click(discard);
  await expect(answer).resolves.toBe(true);
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  expect(leave).toHaveFocus();
});

it('declines on the cancel button, Escape and the backdrop', async () => {
  const { container } = render(<ConfirmHost />);
  for (const decline of [
    () => fireEvent.click(screen.getByRole('button', { name: 'Cancel' })),
    () => fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' }),
    () => fireEvent.mouseDown(container.querySelector('.confirm-overlay')!),
  ]) {
    let answer!: Promise<boolean>;
    act(() => {
      answer = ask('Disable lucas?', { confirm: 'Disable user', cancel: 'Cancel' });
    });
    expect(screen.getByRole('button', { name: 'Disable user' })).toBeInTheDocument();
    decline();
    await expect(answer).resolves.toBe(false);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  }
});

it('declines an unanswered question that a newer one replaces', async () => {
  render(<ConfirmHost />);
  let first!: Promise<boolean>, second!: Promise<boolean>;
  act(() => {
    first = ask('Discard unsaved route moves and open another plan?');
  });
  act(() => {
    second = ask('Discard unsaved record changes?');
  });
  await expect(first).resolves.toBe(false);
  expect(screen.getAllByRole('alertdialog')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  await expect(second).resolves.toBe(true);
});

it('uses the browser dialog when no host is mounted', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  await expect(ask('Discard unsaved record changes?')).resolves.toBe(true);
  expect(confirm).toHaveBeenCalledWith('Discard unsaved record changes?');
});
