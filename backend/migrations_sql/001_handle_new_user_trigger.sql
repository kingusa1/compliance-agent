create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, name, role, active)
  values (
    new.id::text,
    new.email,
    coalesce(
      new.raw_user_meta_data->>'name',
      split_part(new.email, '@', 1),
      new.id::text
    ),
    'reviewer',
    true
  );
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();
