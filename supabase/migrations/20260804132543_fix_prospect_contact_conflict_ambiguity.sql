begin;

create or replace function public.oasis_claim_save_and_promote_prospect_contacts(
    p_current_user_id text,
    p_company_uid text,
    p_company_payload jsonb,
    p_contact_candidates jsonb,
    p_session_id text default null
)
returns table (
    success boolean,
    code text,
    message text,
    assignment_id uuid,
    company_uid text,
    status text,
    assigned_at timestamptz,
    assignment_expires_at timestamptz,
    prospect_id uuid,
    promoted_contact_count integer
)
language plpgsql
volatile
set search_path = public, pg_temp
as $function$
declare
    v_claim record;
    v_candidate jsonb;
    v_contact_type text;
    v_contact_value text;
    v_source_url text;
    v_phone_digits text;
    v_confidence integer;
    v_promoted integer := 0;
    v_company_blocked boolean := false;
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;
    if jsonb_typeof(coalesce(p_contact_candidates, '[]'::jsonb)) <> 'array'
       or jsonb_array_length(coalesce(p_contact_candidates, '[]'::jsonb))
            not between 1 and 8 then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_CONTACT_CANDIDATES';
    end if;

    select *
    into v_claim
    from public.oasis_claim_and_save_company_sales_assignment(
        p_current_user_id,
        p_company_uid,
        p_company_payload,
        p_session_id
    );
    if v_claim.success is not true then
        return query select
            v_claim.success,
            v_claim.code,
            v_claim.message,
            v_claim.assignment_id,
            v_claim.company_uid,
            v_claim.status,
            v_claim.assigned_at,
            v_claim.assignment_expires_at,
            v_claim.prospect_id,
            0;
        return;
    end if;
    if v_claim.prospect_id is null then
        raise exception using
            errcode = 'P0001',
            message = 'OASIS_PROSPECT_ID_REQUIRED';
    end if;

    select exists (
        select 1
        from public.oasis_company_kakao_contact_controls c
        where c.company_uid = v_claim.company_uid
          and c.status in ('opted_out', 'admin_blocked')
    )
    into v_company_blocked;

    for v_candidate in
        select value
        from jsonb_array_elements(p_contact_candidates)
    loop
        if jsonb_typeof(v_candidate) <> 'object' then
            raise exception using
                errcode = '22023',
                message = 'OASIS_INVALID_CONTACT_CANDIDATE';
        end if;
        v_contact_type := lower(btrim(coalesce(
            v_candidate ->> 'contact_type',
            ''
        )));
        v_contact_value := btrim(coalesce(
            v_candidate ->> 'contact_value',
            ''
        ));
        v_source_url := btrim(coalesce(
            v_candidate ->> 'source_url',
            ''
        ));
        if length(v_source_url) > 2000
           or (v_source_url <> '' and v_source_url !~* '^https?://') then
            v_source_url := '';
        end if;

        if v_contact_type = 'phone' then
            v_phone_digits := regexp_replace(
                v_contact_value,
                '[^0-9]',
                '',
                'g'
            );
            if v_phone_digits like '00820%' then
                v_phone_digits := '0' || substr(v_phone_digits, 6);
            elsif v_phone_digits like '0082%' then
                v_phone_digits := '0' || substr(v_phone_digits, 5);
            elsif v_phone_digits like '820%' then
                v_phone_digits := '0' || substr(v_phone_digits, 4);
            elsif v_phone_digits like '82%' then
                v_phone_digits := '0' || substr(v_phone_digits, 3);
            end if;
            if v_phone_digits !~ '^0[0-9]{8,10}$' then
                raise exception using
                    errcode = '22023',
                    message = 'OASIS_INVALID_PHONE_CANDIDATE';
            end if;
            v_contact_value := v_phone_digits;
        elsif v_contact_type = 'email' then
            v_contact_value := lower(v_contact_value);
            if length(v_contact_value) not between 3 and 254
               or v_contact_value ~ '[[:space:]]'
               or position('@' in v_contact_value) <= 1
               or position('.' in split_part(v_contact_value, '@', 2)) = 0 then
                raise exception using
                    errcode = '22023',
                    message = 'OASIS_INVALID_EMAIL_CANDIDATE';
            end if;
        elsif v_contact_type = 'instagram' then
            if length(v_contact_value) not between 1 and 500 then
                raise exception using
                    errcode = '22023',
                    message = 'OASIS_INVALID_INSTAGRAM_CANDIDATE';
            end if;
        else
            raise exception using
                errcode = '22023',
                message = 'OASIS_UNSUPPORTED_CONTACT_CANDIDATE';
        end if;

        v_confidence := case
            when coalesce(v_candidate ->> 'confidence', '') ~ '^[0-9]{1,3}$'
                then least(100, (v_candidate ->> 'confidence')::integer)
            else 0
        end;
        insert into public.oasis_prospect_contacts (
            prospect_id,
            contact_type,
            contact_value,
            contact_label,
            source_type,
            source_url,
            confidence,
            verification_status,
            is_primary,
            owner_user_id,
            metadata,
            do_not_contact,
            collected_at,
            updated_at
        ) values (
            v_claim.prospect_id,
            v_contact_type,
            v_contact_value,
            case v_contact_type
                when 'phone' then 'primary_phone'
                when 'email' then 'primary_email'
                else 'instagram'
            end,
            'operator_approved_sales_collection',
            v_source_url,
            v_confidence,
            'review_required',
            case
                when jsonb_typeof(v_candidate -> 'is_primary') = 'boolean'
                    then (v_candidate ->> 'is_primary')::boolean
                else true
            end,
            p_current_user_id,
            jsonb_build_object(
                'promotion', 'operator_approved_at_save',
                'promotion_version', 'v1042',
                'recipient_consent_recorded', false
            ),
            v_company_blocked,
            now(),
            now()
        )
        on conflict on constraint oasis_prospect_contacts_unique do update
        set
            contact_label = excluded.contact_label,
            source_type = excluded.source_type,
            source_url = coalesce(
                nullif(excluded.source_url, ''),
                public.oasis_prospect_contacts.source_url
            ),
            confidence = greatest(
                public.oasis_prospect_contacts.confidence,
                excluded.confidence
            ),
            verification_status = case
                when public.oasis_prospect_contacts.verification_status in (
                    'manual_verified',
                    'auto_verified',
                    'rejected'
                ) then public.oasis_prospect_contacts.verification_status
                else 'review_required'
            end,
            is_primary = (
                public.oasis_prospect_contacts.is_primary
                or excluded.is_primary
            ),
            owner_user_id = p_current_user_id,
            metadata = (
                public.oasis_prospect_contacts.metadata
                || excluded.metadata
            ),
            do_not_contact = (
                public.oasis_prospect_contacts.do_not_contact
                or excluded.do_not_contact
            ),
            updated_at = now();
        v_promoted := v_promoted + 1;
    end loop;

    return query select
        v_claim.success,
        v_claim.code,
        v_claim.message,
        v_claim.assignment_id,
        v_claim.company_uid,
        v_claim.status,
        v_claim.assigned_at,
        v_claim.assignment_expires_at,
        v_claim.prospect_id,
        v_promoted;
end;
$function$;

revoke all on function public.oasis_claim_save_and_promote_prospect_contacts(
    text, text, jsonb, jsonb, text
) from public, anon, authenticated;
grant execute on function public.oasis_claim_save_and_promote_prospect_contacts(
    text, text, jsonb, jsonb, text
) to service_role;

comment on function public.oasis_claim_save_and_promote_prospect_contacts(
    text, text, jsonb, jsonb, text
) is
    'Atomically claims a sales company, saves the prospect, and promotes operator-approved public contact candidates as review_required without recording recipient consent.';

commit;
