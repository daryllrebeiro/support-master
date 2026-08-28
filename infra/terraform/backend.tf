terraform {
  backend "gcs" {
    # Bucket is supplied dynamically at init time:
    # terraform init -backend-config="bucket=${GOOGLE_CLOUD_PROJECT}-tfstate" -backend-config="prefix=supportmaster/state"
    prefix = "supportmaster/state"
  }
}
