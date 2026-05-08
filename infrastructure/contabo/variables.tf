variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone.DNS edit"
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone id for the apex domain"
  type        = string
}

variable "vps_ipv4" {
  description = "Public IPv4 of the Contabo VPS hosting compliance.<domain>. Maintained manually — Contabo provider too thin to manage VM lifecycle."
  type        = string
}

variable "subdomain" {
  description = "Subdomain under the zone (e.g. \"compliance\" for compliance.example.com, or \"@\" for the apex)"
  type        = string
  default     = "compliance"
}
