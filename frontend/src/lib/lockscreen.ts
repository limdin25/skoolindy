// Simple lock screen config — change password here
export const APP_LOCK_PASSWORD = "5891";
export const DEV_MODE = false; // Set true during development for direct unlock

const STORAGE_KEY = "engageflow_unlocked";
const PASSWORDS_KEY = "engageflow_passwords";

export function getValidPasswords(): string[] {
  try {
    const stored = localStorage.getItem(PASSWORDS_KEY);
    if (stored) {
      const passwords = JSON.parse(stored).filter((p: string) => p.trim() !== "");
      if (passwords.length > 0) return passwords;
    }
  } catch {}
  return [APP_LOCK_PASSWORD];
}

export function isUnlocked(): boolean {
  return sessionStorage.getItem(STORAGE_KEY) === "true" || localStorage.getItem(STORAGE_KEY) === "true";
}

export function checkPassword(input: string): boolean {
  return getValidPasswords().includes(input);
}

export function unlock(remember: boolean): void {
  if (remember) {
    localStorage.setItem(STORAGE_KEY, "true");
  } else {
    sessionStorage.setItem(STORAGE_KEY, "true");
  }
}

export function lock(): void {
  localStorage.removeItem(STORAGE_KEY);
  sessionStorage.removeItem(STORAGE_KEY);
}
