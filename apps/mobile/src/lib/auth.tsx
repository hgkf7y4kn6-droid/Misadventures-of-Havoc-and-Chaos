/**
 * Optional Clerk sign-in. Without EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY everyone plays as a guest;
 * with it, signed-in players keep their seats across devices and reinstalls.
 */
import { ClerkProvider, useAuth } from "@clerk/expo";
import { useHostedAuth } from "@clerk/expo/hosted-auth";
import { tokenCache } from "@clerk/expo/token-cache";
import { useEffect, type ReactNode } from "react";
import { client } from "./client";
import { Button } from "../ui";

export const CLERK_KEY = process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY;

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
    <ClerkProvider publishableKey={CLERK_KEY} tokenCache={tokenCache}>
      <TokenBridge />
      {children}
    </ClerkProvider>
  );
}

function ClerkAccountButton() {
  const { isSignedIn, signOut } = useAuth();
  const { startHostedAuth } = useHostedAuth();
  return isSignedIn
    ? <Button tone="ghost" title="Sign out" onPress={() => void signOut()} />
    : <Button tone="ghost" title="Sign in (optional)" onPress={() => void startHostedAuth()} />;
}

export function AccountButton() {
  return CLERK_KEY ? <ClerkAccountButton /> : null;
}
