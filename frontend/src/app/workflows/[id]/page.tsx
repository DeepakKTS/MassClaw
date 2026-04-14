import { redirect } from "next/navigation";

export default function WorkflowDetailRedirect({ params }: { params: { id: string } }) {
  redirect(`/missions/${params.id}`);
}
