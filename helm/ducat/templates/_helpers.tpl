{{- define "ducat.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ducat.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "ducat.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ducat.labels" -}}
app.kubernetes.io/name: {{ include "ducat.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "ducat.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ducat.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ducat.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "ducat.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "ducat.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}
