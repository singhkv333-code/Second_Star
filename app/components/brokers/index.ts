/**
 * Public surface of the multi-broker onboarding package.
 *
 * AppShell mounts <BrokerOnboarding> (the dialog that replaces the old
 * Kite-only KiteCredentialsPanel) and may drop <BrokerOnboardingBanner> into
 * the shell as a first-run entry point. Everything else is internal.
 */

export {
  BrokerOnboarding,
  BrokerOnboardingBanner,
  type BrokerOAuthResult,
} from "./BrokerOnboarding";
