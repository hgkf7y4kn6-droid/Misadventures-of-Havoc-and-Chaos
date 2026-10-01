/**
 * Optional Clerk sign-in for the web client. Without VITE_CLERK_PUBLISHABLE_KEY everyone plays as a
 * guest (guest sessions are issued by the server), so a party never needs accounts.
 */
import { ClerkProvider, Show, SignInButton, UserButton, useAuth } from "@clerk/react";
import { useEffect, type ReactNode } from "react";
import { client } from "./api";

export const CLERK_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

function TokenBridge() {
  const { isSignedIn, getToken } = useAuth();
  useEffect(() => {
    client.setAuthTokenGetter(isSignedIn ? () => getToken() : undefined);
  }, [isSignedIn, getToken]);
  return null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  if (!CLERK_KEY) return <>{children}</>;
  return (
    <ClerkProvider publishableKey={CLERK_KEY}>
      <TokenBridge />
      {children}
    </ClerkProvider>
  );
}

export function AccountButton() {
  if (!CLERK_KEY) return null;
  return (
    <>
      <Show when="signed-out">
        <SignInButton mode="modal"><button className="btn btn-ghost text-sm">Sign in (optional)</button></SignInButton>
      </Show>
      <Show when="signed-in"><UserButton /></Show>
    </>
  );
}
