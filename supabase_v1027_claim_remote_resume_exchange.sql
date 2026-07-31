-- OASIS CRM v10.2.7
-- A consumed invite token remains a read-only session exchange credential.
-- It cannot create a second job because oasis_claim_remote_consume_invite
-- continues to reject submitted/consumed invites.

begin;

create or replace function public.oasis_claim_remote_resolve_invite(
    p_token_hash text
)
returns table (
    id uuid,
    owner_user_id text,
    status text,
    expires_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_invite public.oasis_claim_remote_invites;
begin
    if lower(trim(p_token_hash)) !~ '^[0-9a-f]{64}$' then
        raise exception 'REMOTE_TOKEN_INVALID';
    end if;

    select i.*
    into v_invite
    from public.oasis_claim_remote_invites i
    where i.token_hash = lower(trim(p_token_hash))
    for update;

    if not found then
        raise exception 'REMOTE_INVITE_NOT_FOUND';
    end if;
    if v_invite.status = 'submitted' then
        return query
        select
            v_invite.id,
            v_invite.owner_user_id,
            v_invite.status,
            v_invite.expires_at;
        return;
    end if;
    if v_invite.status = 'expired' then
        return query
        select
            v_invite.id,
            v_invite.owner_user_id,
            'expired'::text,
            v_invite.expires_at;
        return;
    end if;
    if v_invite.status not in ('created', 'queued', 'sent', 'opened') then
        raise exception 'REMOTE_INVITE_NOT_ACTIVE';
    end if;
    if v_invite.expires_at <= v_now then
        update public.oasis_claim_remote_invites i
        set
            status = 'expired',
            secure_payload_ciphertext = '',
            updated_at = v_now
        where i.id = v_invite.id
        returning i.* into v_invite;
    end if;

    return query
    select
        v_invite.id,
        v_invite.owner_user_id,
        v_invite.status,
        v_invite.expires_at;
end;
$$;


create or replace function public.oasis_claim_remote_mark_invite_opened_global(
    p_token_hash text
)
returns table (
    id uuid,
    owner_user_id text,
    status text,
    expires_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_invite public.oasis_claim_remote_invites;
begin
    if lower(trim(p_token_hash)) !~ '^[0-9a-f]{64}$' then
        raise exception 'REMOTE_TOKEN_INVALID';
    end if;

    select i.*
    into v_invite
    from public.oasis_claim_remote_invites i
    where i.token_hash = lower(trim(p_token_hash))
    for update;

    if not found then
        raise exception 'REMOTE_INVITE_NOT_FOUND';
    end if;
    if v_invite.status = 'submitted' then
        -- Read-only session restoration. Do not mutate the consumed invite.
        return query
        select
            v_invite.id,
            v_invite.owner_user_id,
            v_invite.status,
            v_invite.expires_at;
        return;
    end if;
    if v_invite.status = 'expired' then
        return query
        select
            v_invite.id,
            v_invite.owner_user_id,
            'expired'::text,
            v_invite.expires_at;
        return;
    end if;
    if v_invite.status not in ('created', 'queued', 'sent', 'opened') then
        raise exception 'REMOTE_INVITE_NOT_ACTIVE';
    end if;
    if v_invite.expires_at <= v_now then
        update public.oasis_claim_remote_invites i
        set
            status = 'expired',
            secure_payload_ciphertext = '',
            updated_at = v_now
        where i.id = v_invite.id
        returning i.* into v_invite;
    else
        update public.oasis_claim_remote_invites i
        set
            status = 'opened',
            opened_at = coalesce(i.opened_at, v_now),
            updated_at = v_now
        where i.id = v_invite.id
        returning i.* into v_invite;
    end if;

    return query
    select
        v_invite.id,
        v_invite.owner_user_id,
        v_invite.status,
        v_invite.expires_at;
end;
$$;

revoke execute
on function public.oasis_claim_remote_resolve_invite(text)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_resolve_invite(text)
to service_role;

revoke execute
on function public.oasis_claim_remote_mark_invite_opened_global(text)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_mark_invite_opened_global(text)
to service_role;

commit;
