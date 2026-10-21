'use client';

import {
  createContext,
  useContext,
  useEffect,
  useState,
  ReactNode,
} from 'react';
import type { Session, User } from '@supabase/supabase-js';
import { supabase } from '@/lib/supabaseClient';

interface AuthResult {
  error: string | null;
}

interface AuthContextValue {
  user: User | null;
  session: Session | null;
  loading: boolean;
  initializationError: string | null;
  retryInitialization: () => void;
  signUp: (email: string, password: string) => Promise<AuthResult>;
  signIn: (email: string, password: string) => Promise<AuthResult>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const AUTH_INITIALIZATION_TIMEOUT_MS = 8000;

function safeAuthError(error: unknown): string {
  const value = error as { name?: string; message?: string; status?: number } | null;
  const message = String(value?.message || '').trim();
  const status = Number(value?.status || 0);
  if (/failed to fetch|fetch failed|networkerror|load failed/i.test(message) || (value?.name === 'TypeError' && /fetch/i.test(message))) {
    return 'Unable to reach Supabase Auth. Check the configured project URL and network access, then retry.';
  }
  if (/invalid url/i.test(message)) {
    return 'Supabase Auth configuration is invalid. Check NEXT_PUBLIC_SUPABASE_URL.';
  }
  if (status === 400 && /invalid login credentials/i.test(message)) {
    return 'Email or password is incorrect.';
  }
  if (status === 429) {
    return 'Too many authentication attempts. Wait briefly and try again.';
  }
  if (status >= 500) {
    return 'Supabase Auth is temporarily unavailable. Try again shortly.';
  }
  return message || 'Authentication could not be completed. Please try again.';
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [initializationError, setInitializationError] = useState<string | null>(null);
  const [initializationAttempt, setInitializationAttempt] = useState(0);

  useEffect(() => {
    let mounted = true;
    let completed = false;

    const finish = (newSession: Session | null) => {
      if (!mounted) return;
      completed = true;
      window.clearTimeout(timeout);
      setSession(newSession);
      setUser(newSession?.user ?? null);
      setInitializationError(null);
      setLoading(false);
    };

    const fail = (error: unknown) => {
      if (!mounted) return;
      completed = true;
      window.clearTimeout(timeout);
      setInitializationError(safeAuthError(error));
      setLoading(false);
    };

    const timeout = window.setTimeout(() => {
      if (!mounted || completed) return;
      setInitializationError('Session check timed out. Your saved session was left untouched; retry to check it again.');
      setLoading(false);
    }, AUTH_INITIALIZATION_TIMEOUT_MS);

    let listener: ReturnType<typeof supabase.auth.onAuthStateChange>['data']['subscription'] | null = null;
    try {
      listener = supabase.auth.onAuthStateChange((_event, newSession) => finish(newSession)).data.subscription;
      void supabase.auth.getSession().then(({ data, error }) => {
        if (error) fail(error);
        else finish(data.session);
      }).catch(fail);
    } catch (error) {
      fail(error);
    }

    return () => {
      mounted = false;
      window.clearTimeout(timeout);
      listener?.unsubscribe();
    };
  }, [initializationAttempt]);

  const retryInitialization = () => {
    setInitializationError(null);
    setLoading(true);
    setInitializationAttempt((attempt) => attempt + 1);
  };

  const signUp = async (email: string, password: string): Promise<AuthResult> => {
    try {
      const { error } = await supabase.auth.signUp({ email, password });
      return { error: error ? safeAuthError(error) : null };
    } catch (error) {
      return { error: safeAuthError(error) };
    }
  };

  const signIn = async (email: string, password: string): Promise<AuthResult> => {
    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      return { error: error ? safeAuthError(error) : null };
    } catch (error) {
      return { error: safeAuthError(error) };
    }
  };

  const signOut = async () => {
    await supabase.auth.signOut();
  };

  return (
    <AuthContext.Provider value={{ user, session, loading, initializationError, retryInitialization, signUp, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}
