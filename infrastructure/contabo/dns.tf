# ⚠️ DANGER: This file manages live DNS records pointing customers at production.
# Always `tofu plan` before any merge. A bad apply can break compliance.<domain>
# until DNS propagation completes.

data "cloudflare_zone" "this" {
  zone_id = var.cloudflare_zone_id
}

resource "cloudflare_record" "compliance_apex" {
  zone_id = data.cloudflare_zone.this.id
  name    = var.subdomain
  type    = "A"
  content = var.vps_ipv4
  ttl     = 1
  proxied = true
  comment = "managed by opentofu"
}
