import { AuthForm } from "@/components/AuthForm";

export const metadata = { title: "Sign in · Trip Check" };

export default function SignInPage() {
  return (
    <main className="page">
      <AuthForm kind="signin" />
    </main>
  );
}
