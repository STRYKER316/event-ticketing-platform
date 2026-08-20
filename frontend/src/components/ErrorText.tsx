// Shared rendering for a query/mutation error message.
export function ErrorText({ message }: { message: string }) {
  return <p role="alert">{message}</p>
}
