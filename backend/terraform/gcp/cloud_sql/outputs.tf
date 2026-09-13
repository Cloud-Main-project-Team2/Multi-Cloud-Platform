output "instance_name" {
  value = google_sql_database_instance.this.name
}

output "connection_name" {
  value = google_sql_database_instance.this.connection_name
}

output "region" {
  value = google_sql_database_instance.this.region
}

output "database_version" {
  value = google_sql_database_instance.this.database_version
}

output "public_ip_address" {
  value = google_sql_database_instance.this.public_ip_address
}
