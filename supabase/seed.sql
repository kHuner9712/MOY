-- Optional SQL seed snippet for non-auth bootstrap.
-- Auth users and full demo dataset are created via:
--   npm run seed:demo

insert into public.organizations (id, name, slug)
values ('11111111-1111-1111-1111-111111111111', '桐鸣科技', 'tongming')
on conflict (id) do update
set name = excluded.name,
    slug = excluded.slug;
