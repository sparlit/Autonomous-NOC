"use client"

import * as React from "react"
import { usePathname } from "next/navigation"
import {
  LayoutDashboard,
  Activity,
  AlertTriangle,
  Terminal,
  Settings,
  Shield,
  Network,
  Database,
} from "lucide-react"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarGroup,
  SidebarGroupLabel,
} from "@/components/ui/sidebar"

const data = {
  navMain: [
    {
      title: "Dashboard",
      url: "/",
      icon: LayoutDashboard,
    },
    {
      title: "Monitoring",
      url: "#",
      icon: Activity,
      items: [
        {
          title: "Network Metrics",
          url: "/",
        },
        {
          title: "Agent Operations",
          url: "/agents",
        },
      ],
    },
    {
      title: "Alerts",
      url: "#",
      icon: AlertTriangle,
      items: [
        {
          title: "Active Incidents",
          url: "#",
        },
        {
          title: "Alert History",
          url: "#",
        },
      ],
    },
    {
      title: "Network",
      url: "#",
      icon: Network,
      items: [
        {
          title: "Topology Map",
          url: "/topology",
        },
        {
          title: "Device Inventory",
          url: "#",
        },
      ],
    },
    {
      title: "Automation",
      url: "#",
      icon: Database,
      items: [
        {
          title: "Task Board",
          url: "/tasks",
        },
        {
          title: "Action Logs",
          url: "#",
        },
      ],
    },
    {
      title: "Terminal",
      url: "/terminal",
      icon: Terminal,
    },
  ],
  secondary: [
    {
      title: "Settings",
      url: "#",
      icon: Settings,
    },
    {
      title: "Security",
      url: "#",
      icon: Shield,
    },
  ],
}

/**
 * Renders the application's sidebar navigation, including header, primary navigation with optional submenus, and footer links.
 *
 * Forwards all received props to the underlying `Sidebar` component.
 *
 * @param props - Props passed through to the `Sidebar` component (e.g., className, style, event handlers).
 * @returns The rendered sidebar React element.
 */
export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const isCurrent = (url: string) => url !== "#" && url === pathname

  return (
    <Sidebar variant="inset" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg">
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                <Shield className="size-4" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">Autonomous NOC</span>
                <span className="truncate text-xs">System Administrator</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Platform</SidebarGroupLabel>
          <SidebarMenu>
            {data.navMain.map((item) => (
              <SidebarMenuItem key={item.title}>
                <SidebarMenuButton asChild tooltip={item.title} isActive={isCurrent(item.url)}>
                  <a href={item.url}>
                    {item.icon && <item.icon />}
                    <span>{item.title}</span>
                    {item.title === "Terminal" ? (
                      <span className="ml-auto shrink-0 rounded border border-emerald-500/30 bg-emerald-500/10 px-1 font-mono text-[9px] leading-4 uppercase tracking-wider text-emerald-400">
                        admin
                      </span>
                    ) : null}
                  </a>
                </SidebarMenuButton>
                {item.items?.length ? (
                  <SidebarMenuSub>
                    {item.items.map((subItem) => (
                      <SidebarMenuSubItem key={subItem.title}>
                        <SidebarMenuSubButton
                          href={subItem.url}
                          isActive={isCurrent(subItem.url)}
                        >
                          {subItem.title}
                        </SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    ))}
                  </SidebarMenuSub>
                ) : null}
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          {data.secondary.map((item) => (
            <SidebarMenuItem key={item.title}>
              <SidebarMenuButton asChild size="sm">
                <a href={item.url}>
                  <item.icon />
                  <span>{item.title}</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}
