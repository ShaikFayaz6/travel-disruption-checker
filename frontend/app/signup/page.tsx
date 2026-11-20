import { AuthForm } from "@/components/AuthForm";

export const metadata = { title: "Create an account · Trip Check" };

export default function SignUpPage() {
  return (
    <main className="page">
      <AuthForm kind="signup" />
    </main>
  );
}
