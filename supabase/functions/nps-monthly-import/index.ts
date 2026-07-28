import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const JSON_HEADERS = { "Content-Type": "application/json; charset=utf-8" };

Deno.serve(() => {
  return new Response(
    JSON.stringify({
      status: "disabled",
      message: "국민연금 월별 일괄 업로드가 완료되어 입력창을 닫았습니다.",
    }),
    {
      status: 410,
      headers: JSON_HEADERS,
    },
  );
});
