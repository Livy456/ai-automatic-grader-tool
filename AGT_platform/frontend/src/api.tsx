import axios from "axios";
import { getToken } from "./auth";

export const api = axios.create({ baseURL: "/api" });

api.interceptors.request.use((config) => {
  const t = getToken();
  if (t) config.headers.Authorization = `Bearer ${t}`;
  return config;
});
